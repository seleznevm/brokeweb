import os
from alembic import context
from sqlalchemy import engine_from_config,pool
from backend.models.schema import Base
from backend.backtest import models as backtest_models  # noqa: F401
config=context.config
url=os.getenv('DATABASE_URL',config.get_main_option('sqlalchemy.url'))
if context.is_offline_mode():
    context.configure(url=url,target_metadata=Base.metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine=engine_from_config({'sqlalchemy.url':url},prefix='sqlalchemy.',poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=Base.metadata)
        with context.begin_transaction(): context.run_migrations()
