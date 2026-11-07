def _truthy(value: object) -> bool:
    return str(value).strip().lower() in ("true", "1")
