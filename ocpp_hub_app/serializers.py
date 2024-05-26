# serializers.py
from rest_framework import serializers
from .models import ChargePoint
from .validators import validate_websocket_url

class ChargePointSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChargePoint
        fields = ['id', 'name', 'status', 'location']

class OCPPCredentialsSerializer(serializers.Serializer):
    connect_url = serializers.CharField(validators=[validate_websocket_url])
    cp_id = serializers.CharField(max_length=255)
    auth_key = serializers.CharField(max_length=255)
    sec_prof = serializers.IntegerField()
