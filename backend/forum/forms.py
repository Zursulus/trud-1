from django import forms

from .models import Post, Profile, Report, Topic


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ['title', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 7, 'maxlength': 5000})}


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ['body']
        widgets = {'body': forms.Textarea(attrs={'rows': 5, 'maxlength': 5000})}


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['nickname']
        help_texts = {'nickname': 'Виден всем читателям. ФИО и участок указывайте только по своему желанию.'}


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ['reason']


class ModerationForm(forms.Form):
    action = forms.ChoiceField(choices=[
        ('pin', 'Закрепить / открепить'), ('close', 'Закрыть / открыть'),
        ('hide', 'Скрыть'), ('show', 'Вернуть'),
    ])
    reason = forms.CharField(label='Причина', max_length=300, required=False)

    def clean(self):
        data = super().clean()
        if data.get('action') == 'hide' and not data.get('reason'):
            self.add_error('reason', 'Укажите причину скрытия.')
        return data
