import uuid
from django import template

register = template.Library()


@register.simple_tag
def operation_token():
    return str(uuid.uuid4())
