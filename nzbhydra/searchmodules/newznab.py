import calendar
import datetime
import email
import logging
import re
import time
import xml.etree.ElementTree as ET

import arrow
from requests import RequestException
import requests

from furl import furl

from nzbhydra.database import ProviderApiAccess
from nzbhydra.datestuff import now
from nzbhydra.exceptions import ProviderAuthException, ProviderAccessException, ProviderConnectionException
from nzbhydra.nzb_search_result import NzbSearchResult
from nzbhydra.search_module import SearchModule

logger = logging.getLogger('root')

categories_to_newznab = {
    'All': [],
    'Movies': [2000],
    'Movies HD': [2040, 2050, 2060],
    'Movies SD': [2030],
    'TV': [5000],
    'TV SD': [5030],
    'TV HD': [5040],
    'Audio': [3000],
    'Audio FLAC': [3040],
    'Audio MP3': [3010],
    'Console': [1000],
    'PC': [4000],
    'XXX': [6000],
    'Other': [7000]
}


def get_age_from_pubdate(pubdate):
    timepub = datetime.datetime.fromtimestamp(email.utils.mktime_tz(email.utils.parsedate_tz(pubdate)))
    timenow = now()
    dt = timenow - timepub
    epoch = calendar.timegm(time.gmtime(email.utils.mktime_tz(email.utils.parsedate_tz(pubdate))))
    pubdate_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(email.utils.mktime_tz(email.utils.parsedate_tz(pubdate))))
    age_days = int(dt.days)
    return epoch, pubdate_utc, int(age_days)


def map_category(category):
    #If we know this category we return a list of newznab categories
    if category in categories_to_newznab.keys():
        return categories_to_newznab[category]
    #If not we return an empty list so that we search in all categories
    return []


class NewzNab(SearchModule):
    # todo feature: read caps from server on first run and store them in the config/database
    def __init__(self, provider):
        """

        :type provider: NewznabProvider
        """
        super(NewzNab, self).__init__(provider)
        self.module = "newznab"
        self.category_search = True
        self.limit = 100

    def __repr__(self):
        return "Provider: %s" % self.name

    def build_base_url(self, action, category, o="json", extended=1):
        url = furl(self.provider.settings.get("query_url")).add({"apikey": self.provider.settings.get("apikey"), "o": o, "extended": extended, "t": action, "limit": self.limit, "offset": 0})
        
        categories = map_category(category)
        if len(categories) > 0:
            url.add({"cat": ",".join(str(x) for x in categories)})
        return url

    def get_search_urls(self, args):
        f = self.build_base_url("search", "All")
        if args["query"]:
            f = f.add({"q": args["query"]})
        if args["minsize"]:
            f = f.add({"minsize": args["minsize"]})
        if args["maxsize"]:
            f = f.add({"maxsize": args["maxsize"]})
        if args["maxage"]:
            f = f.add({"age": args["maxage"]})
        return [f.url]

    def get_showsearch_urls(self, args):
        if args["category"] is None:
            args["category"] = "TV"
        
        if args["query"] is None:
            url = self.build_base_url("tvsearch", args["category"])
            if args["rid"] is not None:
                url.add({"rid": args["rid"]})
            if args["tvdbid"] is not None:
                url.add({"tvdbid": args["tvdbid"]})
            if args["episode"] is not None:
                url.add({"ep": args["episode"]})
            if args["season"] is not None:
                url.add({"season": args["season"]})
        else:
            url = self.build_base_url("search", args["category"]).add({"q": args["query"]})

        return [url.url]

    def get_moviesearch_urls(self, args):
        if args["category"] is None:
            args["category"] = "Movies"
        if args["query"] is None:
            url = self.build_base_url("movie", args["category"])
            if args["imdbid"] is not None:
                url.add({"imdbid": args["imdbid"]})
        else:
            url = self.build_base_url("search", args["category"]).add({"q": args["query"]})

        return [url.url]

    test = 0

    def process_query_result(self, json_response, query):
        import json

        entries = []
        queries = []
        json_result = json.loads(json_response)
        total = int(json_result["channel"]["response"]["@attributes"]["total"])
        offset = int(json_result["channel"]["response"]["@attributes"]["offset"])
        if total == 0:
            return {"entries": entries, "queries": []}

        result_items = json_result["channel"]["item"]
        if "title" in result_items:
            # Only one item, put it in a list so the for-loop works
            result_items = [result_items]
        for item in result_items:
            entry = NzbSearchResult()
            entry.title = item["title"]
            entry.link = item["link"]
            entry.pubDate = item["pubDate"]
            pubdate = arrow.get(entry.pubDate, 'ddd, DD MMM YYYY HH:mm:ss Z')
            entry.epoch = pubdate.timestamp
            entry.pubdate_utc = str(pubdate)
            entry.age_days = (arrow.utcnow() - pubdate).days
            entry.precise_date = True
            entry.provider = self.name
            entry.attributes = []

            categories = []
            for i in item["attr"]:
                if i["@attributes"]["name"] == "size":
                    entry.size = int(i["@attributes"]["value"])
                    # entry.sizeReadable = sizeof_readable(entry.size)
                elif i["@attributes"]["name"] == "guid":
                    entry.guid = i["@attributes"]["value"]
                elif i["@attributes"]["name"] == "category":
                    categories.append(int(i["@attributes"]["value"]))
                elif i["@attributes"]["name"] == "poster":
                    entry.poster = (i["@attributes"]["value"])
                # Store all the extra attributes, we will return them later for external apis
                entry.attributes.append({"name": i["@attributes"]["name"], "value": i["@attributes"]["value"]})
            #Map category. Try to find the most specific category (like 2040), then the more general one (like 2000)
            categories = sorted(categories, reverse=True)  # Sort to make the most specific category appear first
            if len(categories) > 0:
                for k, v in categories_to_newznab.items():
                    for c in categories:
                        if c in v:
                            entry.category = k
                            break
                
            entries.append(entry)

        offset += self.limit
        #TODO: dognzb always returns a limit of 100 even if there are more results. Either do some research and get it fixed or load the next page optimistically and see if there are new results, then cancel if not
        if offset < total and offset < 400:
            f = furl(query)
            query = f.remove("offset").add({"offset": offset})
            queries.append(query.url)

            return {"entries": entries, "queries": queries}

        return {"entries": entries, "queries": []}

    def check_auth(self, response):
        body = response.text
        # TODO: unfortunately in case of an auth problem newznab doesn't return json even if requested. So this would be easier/better if we used XML responses instead of json
        if '<error code="100"' in body:
            raise ProviderAuthException("The API key seems to be incorrect.", self)
        if '<error code="101"' in body:
            raise ProviderAuthException("The account seems to be suspended.", self)
        if '<error code="102"' in body:
            raise ProviderAuthException("You're not allowed to use the API.", self)
        if '<error code="910"' in body:
            raise ProviderAccessException("The API seems to be disabled for the moment.", self)
        if '<error code=' in body:
            raise ProviderAccessException("Unknown error while trying to access the provider.", self)

    def get_nfo(self, guid):
        # try to get raw nfo. if it is xml the provider doesn't actually return raw nfos (I'm looking at you, DOGNzb)
        url = furl(self.provider.settings.get("query_url")).add({"apikey": self.provider.settings.get("apikey"), "t": "getnfo", "o": "xml", "id": guid}) #todo: should use build_base_url but that adds search specific stuff
        
        response, papiaccess = self.get_url_with_papi_access(url, "nfo")
        if response is not None:
            nfo = response.text
            if "<?xml" in nfo:
                tree = ET.fromstring(nfo)
                for elem in tree.iter('item'):
                    nfo = elem.find("description").text
                    nfo = re.sub("\\n", nfo, "\n") #TODO: Not completely correct, looks still a bit werid
                    pass
            # otherwise we just hope it's the nfo...
        
            return nfo
        return None
    
    def get_nzb_link(self, guid, title):
        f = furl(self.base_url)
        f.path.add("api")
        f.add({"t": "get", "apikey": self.getsettings["apikey"], "id": guid})
        return f.tostr()


def get_instance(provider):
    return NewzNab(provider)