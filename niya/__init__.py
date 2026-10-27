"""نية (NIYA): لغة برمجة عقودها جزء منها، وأرقامها بتعرف إنها تقديرات."""
from .errors import ContractError, FuelError, HoleError, NiyaError, ParseError
from .interp import Interp

__version__ = "0.2.0"
__all__ = ["Interp", "NiyaError", "ParseError", "ContractError", "HoleError", "FuelError"]
