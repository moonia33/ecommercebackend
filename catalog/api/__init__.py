from .shared import *  # noqa: F401,F403
from .shared import router  # noqa: F401

# Import endpoint modules for router registration side-effects.
from . import back_in_stock  # noqa: F401
from . import products  # noqa: F401
from . import recently_viewed  # noqa: F401
from . import taxonomy  # noqa: F401

__all__ = [n for n in dir() if not n.startswith("__")]
