from django.apps import AppConfig


class ScormStackConfig(AppConfig):
    name = "scormstack_xblock"
    default_auto_field = "django.db.models.BigAutoField"
    plugin_app = {
        "url_config": {
            "cms.djangoapp": {
                "namespace": "scormstack",
                "regex": r"^scormstack/",
                "relative_path": "urls",
            },
        },
    }
