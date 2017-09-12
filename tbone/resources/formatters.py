#!/usr/bin/env python
# encoding: utf-8


import json
import datetime
import decimal
import uuid


class ExtendedJSONEncoder(json.JSONEncoder):
    '''
    Extends the default JSON encoder to support additional data types:
    datetime.datetime
    datetime.date
    datetime.time
    decimal.Decimal
    uuid.UUID
    '''
    def default(self, data):
        if isinstance(data, (datetime.datetime, datetime.date, datetime.time)):
            return data.isoformat()
        elif isinstance(data, decimal.Decimal) or isinstance(data, uuid.UUID):
            return str(data)
        else:
            return super(ExtendedJSONEncoder, self).default(data)


class Formatter(object):
    ''' Base class for all serializers '''
    def parse(self, body):
        raise NotImplementedError()

    def format(self, data):
        raise NotImplementedError()


class JSONFormatter(Formatter):
    ''' Implements JSON serialization '''
    def parse(self, body):
        if isinstance(body, bytes):
            return json.loads(body.decode('utf-8'))
        return json.loads(body)

    def format(self, data):
        return json.dumps(data, cls=ExtendedJSONEncoder)