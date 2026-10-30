class VortaxError(Exception):
    """An expected, user-facing failure. The CLI prints the message and exits 1."""


class MissingToolError(VortaxError):
    def __init__(self, tool: str, hint: str = ""):
        msg = f"required tool not found: {tool}"
        if hint:
            msg += f" ({hint})"
        super().__init__(msg)
        self.tool = tool
