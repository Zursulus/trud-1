from django.urls import path

from . import views

app_name = 'forum'
urlpatterns = [
    path('', views.index, name='index'),
    path('profile/', views.profile, name='profile'),
    path('section/<int:section_id>/', views.section, name='section'),
    path('section/<int:section_id>/new/', views.new_topic, name='new_topic'),
    path('topic/<int:topic_id>/', views.topic, name='topic'),
    path('topic/<int:topic_id>/edit/', views.edit_topic, name='edit_topic'),
    path('post/<int:post_id>/edit/', views.edit_post, name='edit_post'),
    path('topic/<int:topic_id>/report/', views.report_topic, name='report_topic'),
    path('post/<int:post_id>/report/', views.report_post, name='report_post'),
    path('topic/<int:topic_id>/moderate/', views.moderate_topic, name='moderate_topic'),
    path('post/<int:post_id>/moderate/', views.moderate_post, name='moderate_post'),
]
