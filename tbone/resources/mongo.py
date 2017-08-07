#!/usr/bin/env python
# encoding: utf-8


import logging
from functools import singledispatch
from bson.objectid import ObjectId
from bson.errors import InvalidId
from tbone.db.models import post_save
from tbone.resources import Resource
from tbone.resources.http import *

LIMIT = 20
OFFSET = 0
SEARCH_OPERAND = 'q'

logger = logging.getLogger(__file__)

class MongoResource(Resource):

    def __init__(self, *args, **kwargs):
        super(MongoResource, self).__init__(*args, **kwargs)
        self.pk = self.object_class.primary_key
        self.pk_type = self.object_class.primary_key_type
        self.view_type = kwargs.get('view_type', None)
        post_save.connect(self.post_save, sender=self.object_class)

    @property
    def limit(self):
        return LIMIT

    @property
    def offset(self):
        return OFFSET

    async def emit(self, db, key, data):
        pubsub = Channel(db, 'pubsub')
        await pubsub.create_channel()
        pubsub.publish(key, data)

    async def post_save(self, sender, instance, created):
        ''' Receiver function for the object class's post_save signal '''
        if instance.pk is not None:
            # fetch resource (like detail)
            self.db = instance.db
            obj = await self.detail(pk=instance.pk)
            obj['resource_uri'] = '{}{}/'.format(self.get_resource_uri(), instance.pk)
            if created is True and self.view_type == 'list':
                await self.emit(instance.db, 'resource_create', obj)
            elif created is False and self.view_type == 'detail':
                await self.emit(instance.db, 'resource_update', obj)

    async def list(self, *args, **kwargs):
        limit = int(kwargs.pop('limit', [LIMIT])[0])
        if limit == 0:
            limit = 1000
        offset = int(kwargs.pop('offset', OFFSET))
        projection = None
        # perform full text search or standard filtering
        if SEARCH_OPERAND in kwargs.keys():
            filters = {
                '$text': {'$search': kwargs['q']}
            }
            projection = {'score': {'$meta': 'textScore'}}
            sort = [('score', {'$meta': 'textScore'}, )]
        else:
            # build filters from query parameters
            filters = self.build_filters(**kwargs)
            # build sorts from query parameters
            sort = self.build_sort(**kwargs)
        cursor = self.object_class.get_cursor(db=self.db, query=filters, projection=projection, sort=sort)
        cursor.skip(offset)
        cursor.limit(limit)
        total_count = await cursor.count()
        object_list = await self.object_class.find(cursor)
        return {
            'meta': {
                'total_count': total_count,
                'limit': limit,
                'offset': offset
            },
            'objects': [obj.to_data() for obj in object_list]
        }

    async def detail(self, **kwargs):
        try:
            pk = self.pk_type(kwargs['pk'])
            obj = await self.object_class.find_one(self.db, {self.pk: pk})
            if obj:
                return obj.to_data()
            raise NotFound('Object matching the given identifier was not found')
        except InvalidId:
            raise NotFound('Invalid ID')

    async def create(self, **kwargs):
        try:

            obj = self.object_class(self.data)
            # TODO: what about the validate ?
            #await obj.insert(db=self.db)
            await obj.save(db=self.db)
            return obj
        except Exception as ex:
            logger.exception(ex)
            raise BadRequest(ex)

    async def update(self, **kwargs):
        try:
            return MethodNotImplemented()
        except Exception as ex:
            logger.exception(ex)
            raise BadRequest(ex)

    async def modify(self, **kwargs):
        try:
            self.data[self.pk] = self.pk_type(kwargs['pk'])
            result = await self.object_class().update(self.db, data=self.data)
            if result is None:
                raise NotFound('Object matching the given identifier was not found')
            return result.to_data()

        except Exception as ex:
            logger.exception(ex)
            raise BadRequest(ex)

    async def delete(self, *args, **kwargs):
        try:
            pk = self.pk_type(kwargs['pk'])
            await self.object_class.delete_entries(db=self.db, query={self.pk: pk})
        except Exception as ex:
            logger.exception(ex)
            raise BadRequest(ex)

    def serialize(self, method, endpoint, data):
        ''' We override this method to handle schematics object exporting'''
        if isinstance(data, self.object_class):
            data = data.to_data()
        elif isinstance(data, list):
            data = [obj.to_data() for obj in data]
        return super(MongoResource, self).serialize(method, endpoint, data)

    def build_filters(self, **kwargs):
        ''' Break url parameters and turn into filters '''
        filters = {}
        for param, value in kwargs.items():
            # break each url parameter to key + operator (if exists)
            pl = dict(enumerate(param.split('__')))
            key = pl[0]
            operator = pl.get(1, None)
            if key in self.object_class().keys():
                if isinstance(value, list) and operator == 'in':
                    value = [convert_value(v) for v in value]
                else:
                    value = convert_value(value)
                # assign operator, if applicable
                filters[key] = {'${}'.format(operator): value} if operator else value
            elif key == 'created':  # special case where we map `created` key to mongo's _id which also contains a creation timestamp
                dt = parser.parse(convert_value(value))
                dummy_id = ObjectId.from_datetime(dt)
                filters['_id'] = {'${}'.format(operator): dummy_id} if operator else dummy_id
        return filters

    def build_sort(self, **kwargs):
        sort = []
        order = kwargs.get('order_by', None)
        if order:
            if type(order) is list:
                order = order[0]
            order = order.decode('utf-8')
            if order[:1] == '-':
                sort.append((order[1:], -1))
            else:
                sort.append((order, 1))
        return sort

@singledispatch
def convert_value(value):
    ''' Utility functions to convert url params to mongodb filter operators and values '''
    raise NotImplementedError('Cannot convert this {}'.format(type(value)))


@convert_value.register(list)
def _(value):
    return convert_value(value[0])


@convert_value.register(bytes)
def _(value):
    return convert_value(value.decode('utf-8'))


@convert_value.register(ObjectId)
def _(value):
    return value


@convert_value.register(str)
def _(value):
    reserved = {
        '': None,
        'null': None,
        'none': None,
        'true': True,
        'false': False
    }
    if value in reserved:
        return reserved[value]
    # check if value is of type ObjectId
    if ObjectId.is_valid(value):
        return ObjectId(value)
    # check if value is numeric and return a filter which checks both strings and integers
    if value.isnumeric():
        value = {'$in': [int(value), value]}
    # return as string
    return value