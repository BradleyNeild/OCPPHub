from django.apps import AppConfig

class MyAppConfig(AppConfig):
    name = 'ocpp_hub_app'

    def ready(self):
        import ocpp_hub_app.signals
