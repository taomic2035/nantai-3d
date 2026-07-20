from __future__ import annotations

import pytest

from pipeline.mock_layout import MockLayoutGenerator


def _road(layout, road_type: str):
    return next(road for road in layout.roads if road.type == road_type)


@pytest.mark.parametrize("world_seed", [0, 42, 2**31 - 1])
@pytest.mark.parametrize("chunk_y", [-3, 0, 4])
def test_main_road_uses_shared_east_west_boundary_anchors(
    world_seed: int,
    chunk_y: int,
) -> None:
    generator = MockLayoutGenerator(world_seed)

    for chunk_x in range(-4, 4):
        left = generator.generate_chunk(chunk_x, chunk_y)
        right = generator.generate_chunk(chunk_x + 1, chunk_y)
        left_main = _road(left, "main")
        right_main = _road(right, "main")

        assert left_main.points[-1][0] == left.size_m
        assert right_main.points[0][0] == 0
        assert left_main.points[-1][1] == right_main.points[0][1]


@pytest.mark.parametrize("world_seed", [0, 42, 2**31 - 1])
@pytest.mark.parametrize("chunk_x", [-3, 0, 4])
def test_trails_use_shared_north_south_boundary_anchors(
    world_seed: int,
    chunk_x: int,
) -> None:
    generator = MockLayoutGenerator(world_seed)

    for chunk_y in range(-4, 4):
        south = generator.generate_chunk(chunk_x, chunk_y)
        north = generator.generate_chunk(chunk_x, chunk_y + 1)
        south_exits = sorted(
            road.points[-1][0]
            for road in south.roads
            if road.type == "trail" and road.points[-1][1] == south.size_m
        )
        north_entries = sorted(
            road.points[0][0]
            for road in north.roads
            if road.type == "trail" and road.points[0][1] == 0
        )

        assert south_exits
        assert south_exits == north_entries


@pytest.mark.parametrize("world_seed", [0, 42, 2**31 - 1])
def test_stream_corridors_do_not_start_or_stop_at_east_west_chunk_edges(
    world_seed: int,
) -> None:
    generator = MockLayoutGenerator(world_seed)
    rows_with_streams = 0

    for chunk_y in range(-6, 7):
        row = [
            generator.generate_chunk(chunk_x, chunk_y)
            for chunk_x in range(-4, 5)
        ]
        if row[0].water:
            rows_with_streams += 1

        for left, right in zip(row, row[1:], strict=False):
            assert bool(left.water) == bool(right.water)
            if left.water:
                assert left.water[0].points[-1][0] == left.size_m
                assert right.water[0].points[0][0] == 0
                assert left.water[0].points[-1][1] == right.water[0].points[0][1]

    assert rows_with_streams > 0
