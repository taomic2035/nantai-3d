from pipeline.mock_layout import DEFAULT_ASSETS, MockLayoutGenerator


def test_default_chunk_references_every_replaceable_prop_asset():
    layout = MockLayoutGenerator(world_seed=42).generate_chunk(0, 0)

    assert {prop.asset_id for prop in layout.props} == set(DEFAULT_ASSETS["props"])
    assert all(0 <= coordinate <= layout.size_m for prop in layout.props for coordinate in prop.pos)
