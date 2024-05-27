from django import forms
from django.contrib.auth.models import User
from .models import ChargePoint, Authorization

class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(label='Password', widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    password2 = forms.CharField(label='Repeat password', widget=forms.PasswordInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ('username', 'email')
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def clean_password2(self):
        cd = self.cleaned_data
        if cd['password'] != cd['password2']:
            raise forms.ValidationError('Passwords don\'t match.')
        return cd['password2']

class ChargePointForm(forms.ModelForm):
    class Meta:
        model = ChargePoint
        fields = ['name', 'location']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'location': forms.TextInput(attrs={'class': 'form-control'}),
        }

class AuthorizationForm(forms.ModelForm):
    class Meta:
        model = Authorization
        fields = ['csms_name', 'connect_url', 'cp_id', 'auth_key', 'sec_prof']
        widgets = {
            'csms_name': forms.TextInput(attrs={'class': 'form-control'}),
            'connect_url': forms.TextInput(attrs={'class': 'form-control'}),
            'cp_id': forms.TextInput(attrs={'class': 'form-control'}),
            'auth_key': forms.TextInput(attrs={'class': 'form-control'}),
            'sec_prof': forms.NumberInput(attrs={'class': 'form-control'}),
        }

class ResendVerificationEmailForm(forms.Form):
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={'class': 'form-control'}))
