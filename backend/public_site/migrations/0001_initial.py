import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import public_site.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PublicDocumentCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True, verbose_name='Категория')),
                ('active', models.BooleanField(default=True, verbose_name='Можно выбирать')),
                ('sort_order', models.PositiveIntegerField(default=100, verbose_name='Порядок')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
            ],
            options={
                'verbose_name': 'Категория публичного документа',
                'verbose_name_plural': 'Категории публичных документов',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='PublicNews',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=180, verbose_name='Заголовок')),
                ('category', models.CharField(default='НОВОСТЬ', max_length=80, verbose_name='Рубрика')),
                ('summary', models.CharField(max_length=400, verbose_name='Краткий анонс')),
                ('body', models.TextField(max_length=12000, verbose_name='Текст публикации')),
                ('published_on', models.DateField(default=django.utils.timezone.localdate, verbose_name='Дата публикации')),
                ('is_featured', models.BooleanField(default=False, verbose_name='Выделить первой')),
                ('is_published', models.BooleanField(default=False, verbose_name='Опубликовано')),
                ('public_checked', models.BooleanField(default=False, editable=False, verbose_name='Проверено для публикации')),
                ('published_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Подтверждено к публикации')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('published_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='published_public_news', to=settings.AUTH_USER_MODEL, verbose_name='Подтвердил')),
            ],
            options={
                'verbose_name': 'Новость сайта',
                'verbose_name_plural': 'Новости сайта',
                'ordering': ['-is_featured', '-published_on', '-id'],
            },
        ),
        migrations.CreateModel(
            name='PublicDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200, verbose_name='Название')),
                ('description', models.CharField(blank=True, max_length=500, verbose_name='Краткое описание')),
                ('document', models.FileField(max_length=300, upload_to=public_site.models.public_document_path, verbose_name='Файл')),
                ('original_name', models.CharField(editable=False, max_length=255, verbose_name='Исходное имя файла')),
                ('file_size', models.PositiveBigIntegerField(editable=False, verbose_name='Размер, байт')),
                ('document_date', models.DateField(default=django.utils.timezone.localdate, verbose_name='Дата документа')),
                ('is_published', models.BooleanField(default=False, verbose_name='Опубликовано')),
                ('public_checked', models.BooleanField(default=False, editable=False, verbose_name='Проверено для публикации')),
                ('published_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Подтверждено к публикации')),
                ('notes', models.TextField(blank=True, verbose_name='Служебное примечание')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='documents', to='public_site.publicdocumentcategory', verbose_name='Категория')),
                ('published_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='published_public_documents', to=settings.AUTH_USER_MODEL, verbose_name='Подтвердил')),
            ],
            options={
                'verbose_name': 'Публичный документ',
                'verbose_name_plural': 'Публичные документы',
                'ordering': ['-document_date', '-id'],
            },
        ),
    ]
