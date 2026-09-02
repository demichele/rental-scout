"""Errores de adapters de portales."""


class AdapterFetchError(RuntimeError):
    """Un portal falló al traer avisos; el pipeline puede seguir con los demás."""