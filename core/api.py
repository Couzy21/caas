from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler
from rest_framework.exceptions import APIException
from rest_framework.pagination import PageNumberPagination

class Problem(APIException):
    def __init__(self, message, status=400):
        self.status_code = status
        super().__init__(message)

def exception_handler(exc, context):
    response = drf_handler(exc, context)
    if response is not None:
        response.data = {'message': 'Request failed.', 'errors': response.data}
    return response

def ok(data=None, message='Success.', status=200):
    return Response({'message': message, 'data': data}, status=status)

class Pagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100
    page_size_query_param = 'page_size'
    def get_paginated_response(self, data):
        return Response({'message': 'Success.', 'count': self.page.paginator.count, 'next': self.get_next_link(),
                         'previous': self.get_previous_link(), 'results': data})
