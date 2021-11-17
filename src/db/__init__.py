from tortoise import Tortoise
from aerich import Command

from . import config, models
from src import env, log
from . import effective_utils

logger = log.getLogger('RSStT.db')

User = models.User
Feed = models.Feed
Sub = models.Sub
Option = models.Option


async def init():
    if env.DB_URL.startswith('sqlite'):
        aerich_command = Command(tortoise_config=config.TORTOISE_ORM, location='src/db/migrations_sqlite')
    elif env.DB_URL.startswith('postgres'):
        aerich_command = Command(tortoise_config=config.TORTOISE_ORM, location='src/db/migrations_postgres')
    else:
        aerich_command = None
        logger.critical('INVALID DB SCHEME! ONLY "sqlite" AND "postgres" ARE SUPPORTED!')
        exit(1)
    await aerich_command.init()
    await aerich_command.upgrade()
    # await Tortoise.init(config=config.TORTOISE_ORM)
    await effective_utils.init()
    logger.info('Successfully connected to the DB')


async def close():
    await Tortoise.close_connections()
