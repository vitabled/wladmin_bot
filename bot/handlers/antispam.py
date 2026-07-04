"""Antispam message handler."""

from aiogram import Router, types
from aiogram.filters import StateFilter

router = Router()


@router.message(StateFilter(None))
async def process_message(message: types.Message):
    """Process regular messages for antispam."""
    # Antispam logic will be implemented here
    pass
