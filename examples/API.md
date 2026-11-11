# CAAS API examples

Run against the explicitly local demo first. Replace uppercase placeholders. UUID fields must contain real UUIDs. JSON responses always include `message`, except the raw schema and documentation pages.

```sh
export CAAS_URL=http://localhost:8000
curl -sS "$CAAS_URL/api/v1/auth/register/" -H 'Content-Type: application/json' \
  -d '{"username":"shopowner","email":"owner@example.test","password":"change-to-a-strong-password-284!","organisation_name":"Lagos Shop"}'
```

The registration response contains `data.access`, `data.refresh`, `data.user`, and `data.organisation`. Copy the access token into your shell; keep tokens out of shell history in production.

```sh
export CAAS_JWT='YOUR_ACCESS_TOKEN'
curl -sS "$CAAS_URL/api/v1/auth/login/" -H 'Content-Type: application/json' \
  -d '{"username":"shopowner","password":"change-to-a-strong-password-284!"}'
curl -sS "$CAAS_URL/api/v1/auth/refresh/" -H 'Content-Type: application/json' -d '{"refresh":"YOUR_REFRESH_TOKEN"}'
curl -sS "$CAAS_URL/api/v1/organisation/" -H "Authorization: Bearer $CAAS_JWT"
curl -sS "$CAAS_URL/api/v1/bots/" -H "Authorization: Bearer $CAAS_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"Lagos Shop Assistant","description":"Shop support","allowed_origins":["http://localhost:8000"]}'
export CAAS_BOT='YOUR_BOT_UUID'
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/sources/" -H "Authorization: Bearer $CAAS_JWT" -H 'Content-Type: application/json' \
  -d '{"title":"Delivery FAQ","kind":"faq","text":"What is the delivery fee? Delivery within Lagos costs 2500 naira and takes 2 to 3 business days."}'
```

Save `data.id` as the source ID; poll until `data.status` is `ready`. `failed` includes a safe error message. Edit the source to retry.

```sh
export CAAS_SOURCE='YOUR_SOURCE_UUID'
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/sources/$CAAS_SOURCE/" -H "Authorization: Bearer $CAAS_JWT"
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/sources/" -H "Authorization: Bearer $CAAS_JWT" \
  -F 'title=Product catalogue' -F 'kind=pdf' -F 'file=@catalogue.pdf;type=application/pdf'
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/chat/" -H "Authorization: Bearer $CAAS_JWT" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: delivery-001' -d '{"message":"What is the delivery fee?"}'
```

Example demo response (IDs vary):

```json
{
  "message": "Answer ready.",
  "data": {
    "answer": "[LOCAL DEMO — lexical retrieval, no live AI] What is the delivery fee? Delivery within Lagos costs 2500 naira and takes 2 to 3 business days.",
    "conversation_id": "00000000-0000-0000-0000-000000000001",
    "sources": [{"source_id":"00000000-0000-0000-0000-000000000002","title":"Delivery FAQ","chunk":0}],
    "usage": {"billable_answer":true,"input_tokens":0,"output_tokens":0,"cost_microusd":0,"cost_is_upper_bound":false,"mode":"local-demo-no-ai"}
  }
}
```

Continue with a **new** idempotency key and `conversation_id` from that response. Retrying the original key/body returns the original answer without extra usage. A different body under that key returns 409.

```sh
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/chat/" -H "Authorization: Bearer $CAAS_JWT" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: delivery-002' \
  -d '{"message":"How long does delivery take?","conversation_id":"YOUR_CONVERSATION_UUID"}'
curl -sS "$CAAS_URL/api/v1/keys/" -H "Authorization: Bearer $CAAS_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"Production server","bot_id":"YOUR_BOT_UUID","scopes":["chat:write","conversations:read"],"expires_at":"2027-12-01T00:00:00Z"}'
export CAAS_API_KEY='YOUR_ONCE_SHOWN_SECRET'
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/chat/" -H "Authorization: ApiKey $CAAS_API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: server-001' -d '{"message":"What is the delivery fee?"}'
curl -sS "$CAAS_URL/api/v1/conversations/?page=1&page_size=20" -H "Authorization: ApiKey $CAAS_API_KEY"
curl -sS "$CAAS_URL/api/v1/usage/" -H "Authorization: Bearer $CAAS_JWT"
curl -sS "$CAAS_URL/api/v1/plans/"
curl -sS "$CAAS_URL/api/v1/billing/checkout/" -H "Authorization: Bearer $CAAS_JWT" -H 'Content-Type: application/json' -d '{"plan":"starter"}'
```

Checkout returns a reference and Paystack-hosted HTTPS checkout URL only when configured. It grants no access until server verification. This purchases 30 days with manual renewal. Use only Paystack test credentials until you explicitly authorise live billing.

```sh
curl -sS "$CAAS_URL/api/v1/keys/YOUR_KEY_UUID/rotate/" -X POST -H "Authorization: Bearer $CAAS_JWT"
curl -sS "$CAAS_URL/api/v1/keys/YOUR_KEY_UUID/revoke/" -X POST -H "Authorization: Bearer $CAAS_JWT"
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/sources/$CAAS_SOURCE/" -X PATCH -H "Authorization: Bearer $CAAS_JWT" \
  -H 'Content-Type: application/json' -d '{"text":"Delivery in Lagos now costs 3000 naira."}'
curl -sS "$CAAS_URL/api/v1/bots/$CAAS_BOT/sources/$CAAS_SOURCE/" -X DELETE -H "Authorization: Bearer $CAAS_JWT"
```

A visitor session uses no customer API secret:

```sh
curl -sS "$CAAS_URL/api/v1/widget/$CAAS_BOT/session/" -H 'Content-Type: application/json' -d '{"origin":"http://localhost:8000"}'
export CAAS_VISITOR='TOKEN_FROM_SESSION_RESPONSE'
curl -sS "$CAAS_URL/api/v1/widget/$CAAS_BOT/chat/" -H "X-Visitor-Token: $CAAS_VISITOR" \
  -H 'X-Website-Origin: http://localhost:8000' -H 'Idempotency-Key: visitor-001' -H 'Content-Type: application/json' -d '{"message":"What is the delivery fee?"}'
```

Visitor tokens expire after 15 minutes and cannot authenticate management APIs. Install the loader snippet in the README to use the iframe UI. Origin checks do not authenticate a non-browser client; quotas, cost ceilings and edge abuse controls are still required.
