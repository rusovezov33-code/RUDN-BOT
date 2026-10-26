"""Точка входа (Railway: `python bot.py`). Вся логика — в пакете rudn/."""
import asyncio

from rudn.core.app import main

if __name__ == "__main__":
    asyncio.run(main())
