# decorators.py
def method_scopes(scopes):
    def decorator(func):
        func.required_scopes_per_method = scopes
        return func
    return decorator
