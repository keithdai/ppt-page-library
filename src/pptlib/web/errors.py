from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pptlib.domain.errors import AppError, ErrorCode
from pptlib.domain.ids import new_id

logger = logging.getLogger("pptlib.web")


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", new_id("req")))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool = False,
    details: object | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": code.value,
                "message": message,
                "retryable": retryable,
                "details": jsonable_encoder(details if details is not None else {}),
            },
            "request_id": _request_id(request),
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        return _error_response(
            request,
            status_code=409,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=dict(error.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code=ErrorCode.REQUEST_INVALID,
            message="request validation failed",
            details=error.errors(),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if error.status_code == 404 else ErrorCode.INTERNAL_ERROR
        return _error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=str(error.detail),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception(
            "unexpected web exception",
            exc_info=(type(error), error, error.__traceback__),
            extra={
                "request_id": _request_id(request),
            },
        )
        return _error_response(
            request,
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="internal server error",
            retryable=False,
        )
