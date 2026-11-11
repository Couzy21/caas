import json
import logging

class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Deliberately exclude free-form text, request data, URLs and exceptions.
        return json.dumps({'level': record.levelname, 'logger': record.name, 'event': 'application_log'})
