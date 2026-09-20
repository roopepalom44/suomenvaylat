# -*- coding: utf-8 -*-
"""Vale-HTTP-kerros työkalulaatikon verkkopolun testaamiseen.

Työkalun sivutus, uudelleenyritys ja CQL→BBOX-varareitti olivat aiemmin
testattavissa vain ajamalla työkalu ArcGIS Prossa oikeaa palvelua vasten.
Tämä moduuli korvaa ``HttpTransport``-luokan skriptatulla vastausjonolla,
joten koko verkkopolku on testattavissa ilman arcpyä ja ilman verkkoa.
"""

import json
import urllib.parse


class FakeResponseHeaders(dict):
    """Pienin mahdollinen ``http.client``-headerien korvike."""

    def get(self, key, default=None):
        for existing_key, value in self.items():
            if existing_key.lower() == str(key).lower():
                return value
        return default


class FakeResponse(object):
    """Yksi skriptattu vastaus.

    ``body`` voi olla str, bytes tai dict (joka serialisoidaan JSONiksi).
    ``error`` nostetaan pyynnön sijaan, jolloin uudelleenyrityslogiikka
    aktivoituu.
    """

    def __init__(self, body=None, status=200, content_type="application/json",
                 error=None, headers=None, read_seconds=0.0):
        self.status = status
        self.error = error
        self.read_seconds = read_seconds
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body if body is not None else b""
        self.headers = FakeResponseHeaders(headers or {})
        if content_type is not None:
            self.headers.setdefault("Content-Type", content_type)


class FakeTransport(object):
    """Korvaa ``HttpTransport``in ja tallentaa kaikki tehdyt pyynnöt."""

    def __init__(self, responses=None, handler=None):
        self.queue = list(responses or [])
        self.handler = handler
        self.requests = []
        self.default = FakeResponse({"features": []})

    def _next_response(self, url, method, headers, body):
        if self.handler is not None:
            result = self.handler(url, method, headers, body)
            if result is not None:
                return result
        if self.queue:
            return self.queue.pop(0)
        return self.default

    def request(self, url, method="GET", headers=None, body=None, timeout=60):
        self.requests.append({
            "url": url,
            "method": method,
            "headers": dict(headers or {}),
            "body": body,
            "timeout": timeout,
            "query": dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)),
            "form": dict(urllib.parse.parse_qsl(body.decode("utf-8")))
            if isinstance(body, bytes) and method == "POST" else {},
        })
        response = self._next_response(url, method, headers, body)
        if response.error is not None:
            raise response.error
        return response.status, response.headers, response.body, response.read_seconds

    def close_all(self):
        pass

    # --- testien apurit ---

    @property
    def call_count(self):
        return len(self.requests)

    def start_indexes(self):
        """Palauta jokaisen WFS-pyynnön startIndex numerojärjestyksessä."""
        values = []
        for entry in self.requests:
            raw = entry["query"].get("startIndex") or entry["form"].get("startIndex")
            if raw is not None:
                values.append(int(raw))
        return values


def feature_page(count, start=0, geometry_name="geometry"):
    """Muodosta GeoJSON-sivu, jossa on ``count`` yksinkertaista pistettä."""
    return {
        "type": "FeatureCollection",
        "geometry_name": geometry_name,
        "features": [
            {
                "type": "Feature",
                "id": "f{}".format(start + index),
                "geometry": {"type": "Point", "coordinates": [index, start]},
                "properties": {"idx": start + index},
            }
            for index in range(count)
        ],
    }


def paged_handler(total_features, page_size, geometry_name="geometry"):
    """Palauta handler, joka sivuttaa ``total_features`` kohdetta startIndexillä."""

    def handler(url, method, headers, body):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if method == "POST" and isinstance(body, bytes):
            query.update(dict(urllib.parse.parse_qsl(body.decode("utf-8"))))
        start = int(query.get("startIndex", 0))
        remaining = max(0, total_features - start)
        count = min(page_size, remaining)
        return FakeResponse(feature_page(count, start, geometry_name))

    return handler
