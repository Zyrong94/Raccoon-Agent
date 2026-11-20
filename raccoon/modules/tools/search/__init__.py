"""结构化网页搜索接口与引擎。"""

from raccoon.modules.tools.search.base import SearchEngine, SearchResult
from raccoon.modules.tools.search.bing import BingSearch
from raccoon.modules.tools.search.ddg import DDGSearch
from raccoon.modules.tools.search.exa import ExaSearch
from raccoon.modules.tools.search.multie import MultieSearch
from raccoon.modules.tools.search.parallel import ParallelSearch
from raccoon.modules.tools.search.serper import SerperSearch
from raccoon.modules.tools.search.sogou import SogouSearch
from raccoon.modules.tools.search.tavily import TavilySearch
from raccoon.modules.tools.search.discovery import create_tool_search, search_tools

__all__ = ["SearchEngine", "SearchResult", "ExaSearch", "TavilySearch", "DDGSearch", "ParallelSearch", "SerperSearch", "BingSearch", "SogouSearch", "MultieSearch", "create_tool_search", "search_tools"]
