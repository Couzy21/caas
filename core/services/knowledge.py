import re
from io import BytesIO
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from pypdf import PdfReader
from core.api import Problem
from core.models import Organisation, KnowledgeSource, DocumentChunk
from . import providers
from .usage import reserve_embedding, finish_embedding


def extract_pdf(data):
    if len(data) > settings.MAX_UPLOAD_BYTES or not data.startswith(b'%PDF-'):
        raise Problem('Upload a PDF of at most 5 MiB.')
    try:
        pdf = PdfReader(BytesIO(data))
        if pdf.is_encrypted:
            raise Problem('Encrypted PDFs are unsupported.')
        if len(pdf.pages) > 100:
            raise Problem('PDF exceeds 100 pages.')
        parts = []
        length = 0
        for page in pdf.pages:
            text = page.extract_text() or ''
            length += len(text)
            if length > settings.MAX_SOURCE_CHARS:
                raise Problem('Extracted PDF text exceeds 100,000 characters.')
            parts.append(text)
        text = '\n'.join(parts).strip()
        if len(text) < 20:
            raise Problem('PDF contains too little selectable text. Scanned PDFs require OCR and are unsupported.')
        return text
    except Problem:
        raise
    except Exception:
        raise Problem('PDF could not be read. Use an unencrypted text-based PDF.') from None


def validate_content(data, existing=None):
    kind = data.get('kind', existing.kind if existing else 'text')
    file = data.get('file')
    text = data.get('text', existing.text if existing else '').strip()
    if kind == 'pdf':
        if not file:
            if existing and existing.kind == 'pdf' and 'text' not in data:
                return existing.text, existing.size_bytes
            raise Problem('PDF sources require a PDF file.')
        data_bytes = file.read(settings.MAX_UPLOAD_BYTES+1)
        file.seek(0)
        text = extract_pdf(data_bytes)
        size = len(data_bytes) + len(text.encode())
    else:
        if file:
            raise Problem('Files are accepted only for PDF sources.')
        if not text or len(text) > settings.MAX_SOURCE_CHARS:
            raise Problem('Text must contain 1 to 100,000 characters. FAQs should include question and answer.')
        size = len(text.encode())
    return text, size


@transaction.atomic
def save_source(org, bot, validated, existing=None):
    Organisation.objects.select_for_update().get(pk=org.pk)
    text, size = validate_content(validated, existing)
    sources = KnowledgeSource.objects.filter(organisation=org)
    if existing:
        existing = sources.select_for_update().get(pk=existing.pk)
        sources = sources.exclude(pk=existing.pk)
    if sources.count() >= 100 or (sources.aggregate(n=Sum('size_bytes'))['n'] or 0) + size > settings.MAX_ORG_STORAGE_BYTES:
        raise Problem('Organisation knowledge storage limit reached.', 413)
    source = existing or KnowledgeSource(organisation=org, bot=bot)
    if existing:
        source.chunks.all().delete()
        source.revision += 1
    for key in ['title','kind']:
        if key in validated:
            setattr(source, key, validated[key])
    if validated.get('file'):
        if source.file:
            old_name = source.file.name
            transaction.on_commit(lambda: source.file.storage.delete(old_name))
        source.file = validated['file']
    elif source.kind != 'pdf' and source.file:
        old_name = source.file.name
        transaction.on_commit(lambda: source.file.storage.delete(old_name))
        source.file = ''
    source.text, source.size_bytes = text, size
    source.status, source.error, source.lease_until, source.attempts = 'queued', '', None, 0
    source.save()
    from core.tasks import process_source
    transaction.on_commit(lambda: enqueue(process_source, str(org.pk), str(source.pk), source.revision))
    return source


def enqueue(task, *args):
    # The database queued state is the durable outbox; beat recovers broker publication failures.
    try:
        task.apply_async(args=args, retry=False)
    except Exception:
        pass


def split_text(text):
    chunks = [text[i:i+800] for i in range(0, len(text), 700)]
    if len(chunks) > settings.MAX_CHUNKS:
        raise Problem('Document exceeds chunk-count limit.')
    return chunks


def build_chunks(source):
    texts = split_text(source.text)
    attempt = None
    if not settings.DEMO_MODE:
        # UTF-8 byte count bounds input token count conservatively.
        bound = sum(len(t.encode()) for t in texts) * settings.EMBED_MICROUSD_PER_TOKEN
        attempt = reserve_embedding(source.organisation, bound)
    try:
        vectors, tokens = providers.embed(texts)
        if attempt:
            finish_embedding(attempt, tokens * settings.EMBED_MICROUSD_PER_TOKEN, tokens)
    except Exception:
        if attempt:
            finish_embedding(attempt)
        raise
    return [DocumentChunk(organisation_id=source.organisation_id, bot_id=source.bot_id, source=source,
            position=i, text=t, embedding=vectors[i], embedding_model='demo-lexical' if settings.DEMO_MODE else settings.EMBEDDING_MODEL,
            metadata={'title':source.title,'position':i}) for i,t in enumerate(texts)]


def retrieve(org, bot, question):
    qs = DocumentChunk.objects.filter(organisation=org, bot=bot, source__organisation=org, source__bot=bot, source__status='ready')
    if settings.DEMO_MODE:
        stop = {'what','when','where','does','have','your','with','that','this','from','about','the','are','can','you','our','and','for','how','is','do','a','to','in','of'}
        words = set(re.findall(r'\w+', question.lower())) - stop
        ranked = sorted(((len(words & set(re.findall(r'\w+', c.text.lower()))), c) for c in qs), key=lambda x:x[0], reverse=True)
        return [c for score,c in ranked[:5] if score > 0], 0
    vectors, tokens = providers.embed([question])
    from pgvector.django import CosineDistance
    chunks = list(qs.filter(embedding_model=settings.EMBEDDING_MODEL).exclude(embedding__isnull=True)
                  .annotate(distance=CosineDistance('embedding',vectors[0])).filter(distance__lt=0.65).order_by('distance')[:5])
    return chunks, tokens
