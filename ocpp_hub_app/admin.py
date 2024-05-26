# admin.py
from django.contrib import admin
from .models import ChargePoint

@admin.register(ChargePoint)
class ChargePointAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'location', 'user')
    search_fields = ('name', 'status', 'location', 'user__username')
