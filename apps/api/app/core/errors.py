"""Typed API error responses with problem-details style payloads."""

from fastapi import HTTPException, status


class GatewayError(HTTPException):
    def __init__(self, code: str, message: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        self.code = code
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def not_found(code: str, message: str) -> GatewayError:
    return GatewayError(code, message, status_code=status.HTTP_404_NOT_FOUND)


def conflict(code: str, message: str) -> GatewayError:
    return GatewayError(code, message, status_code=status.HTTP_409_CONFLICT)
