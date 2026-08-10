import django_filters

from common.choices import VideoAccessLevel

from .models import Video


class VideoFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(field_name='category__slug')
    program = django_filters.CharFilter(field_name='program__slug')
    access_level = django_filters.ChoiceFilter(choices=VideoAccessLevel.choices)
    is_featured = django_filters.BooleanFilter()

    class Meta:
        model = Video
        fields = ['category', 'program', 'access_level', 'is_featured']
