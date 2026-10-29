"""Validate the supported JSON-schema subset with no runtime dependencies."""
import math


TYPES = {'object':dict, 'array':list, 'string':str, 'integer':int, 'number':(int,float), 'boolean':bool}


def check_schema(schema):
    if not isinstance(schema, dict) or schema.get('type') not in TYPES:
        raise ValueError('Tool schema needs a supported type')
    allowed = {'type','description','enum'}
    kind = schema['type']
    if kind == 'object':
        allowed |= {'properties','required','additionalProperties'}
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        if (not isinstance(properties, dict) or not all(isinstance(key, str) for key in properties)
                or not isinstance(required, list) or not all(isinstance(key, str) for key in required)
                or len(set(required)) != len(required) or any(key not in properties for key in required)):
            raise ValueError('Invalid object schema')
        if schema.get('additionalProperties') is not False:
            raise ValueError('Tool objects must reject additional properties')
        for item in properties.values():
            check_schema(item)
    elif kind == 'array':
        allowed |= {'items','minItems','maxItems'}
        check_schema(schema['items'])
    elif kind == 'string':
        allowed |= {'minLength','maxLength'}
    elif kind in ('integer','number'):
        allowed |= {'minimum','maximum'}
    if set(schema)-allowed:
        raise ValueError('Unsupported schema keywords: '+str(sorted(set(schema)-allowed)))
    for key in ('minItems','maxItems','minLength','maxLength'):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError('Invalid schema bound: '+key)
    for key in ('minimum','maximum'):
        if key in schema and (type(schema[key]) not in (int,float) or not math.isfinite(schema[key])):
            raise ValueError('Invalid schema bound: '+key)
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ValueError('Schema enum must be nonempty')


def validate(schema, value, path='args'):
    kind = schema['type']
    if not isinstance(value, TYPES[kind]) or (kind in ('integer','number') and isinstance(value, bool)):
        raise ValueError(path+' must be '+kind)
    if kind == 'number' and not math.isfinite(value):
        raise ValueError(path+' must be finite')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path+' has an unsupported value')
    if kind == 'object':
        properties = schema.get('properties', {})
        if set(value)-set(properties):
            raise ValueError(path+' has unexpected properties: '+str(sorted(set(value)-set(properties))))
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError(path+' is missing required properties')
        for key,item in value.items():
            validate(properties[key], item, path+'.'+key)
    elif kind == 'array':
        for index,item in enumerate(value):
            validate(schema['items'], item, path+'['+str(index)+']')
        _range(schema, len(value), 'minItems','maxItems',path)
    elif kind == 'string':
        _range(schema, len(value), 'minLength','maxLength',path)
    elif kind in ('integer','number'):
        _range(schema, value, 'minimum','maximum',path)


def _range(schema, value, lower, upper, path):
    if (lower in schema and value < schema[lower]) or (upper in schema and value > schema[upper]):
        raise ValueError(path+' is outside the allowed range')


def parameters(properties=None, required=()):
    return {'type':'object', 'properties':properties or {}, 'required':list(required), 'additionalProperties':False}


TEXT = {'type':'string'}
CHECKS = {'type':'array', 'items':parameters({'id':TEXT, 'statement':TEXT, 'source_quote':TEXT}, ('id','statement'))}
