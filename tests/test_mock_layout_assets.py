import math

import pytest
from pydantic import ValidationError

from pipeline.mock_layout import DEFAULT_ASSETS, MockLayoutGenerator
from pipeline.schema import GeoOrigin


def test_default_chunk_references_every_replaceable_prop_asset():
    layout = MockLayoutGenerator(world_seed=42).generate_chunk(0, 0)

    assert {prop.asset_id for prop in layout.props} == set(DEFAULT_ASSETS["props"])
    assert all(0 <= coordinate <= layout.size_m for prop in layout.props for coordinate in prop.pos)


class TestGeoOriginBounds:
    """L2 布局的 GeoOrigin 从外部 layout JSON 加载 (ChunkLayout(**json.loads(...)))，
    应与坐标信任根 recon_schema.GeoAnchor 同样拒绝越界/非有限 GPS，避免越界地理
    原点被静默接受。文档化的 chunk 范围 (±10^4) 下 mock_layout 产出的 lat/lon 均在界内。"""

    def test_valid_origin_constructs(self):
        origin = GeoOrigin(lat=26.0, lon=119.0, alt=50.0)
        assert (origin.lat, origin.lon, origin.alt) == (26.0, 119.0, 50.0)

    @pytest.mark.parametrize("lat,lon", [(200, 0), (-91, 0), (0, 999), (0, -181)])
    def test_out_of_range_gps_is_rejected(self, lat, lon):
        with pytest.raises(ValidationError):
            GeoOrigin(lat=lat, lon=lon, alt=0.0)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_gps_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            GeoOrigin(lat=bad, lon=0.0, alt=0.0)
        with pytest.raises(ValidationError):
            GeoOrigin(lat=0.0, lon=bad, alt=0.0)
        with pytest.raises(ValidationError):
            GeoOrigin(lat=0.0, lon=0.0, alt=bad)
