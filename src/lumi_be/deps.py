from typing import Annotated

from fastapi import Depends, Request


def get_db(request: Request):
    return request.app.state.db


DB = Annotated[object, Depends(get_db)]
