"""Testes unitários da conversão de segundos para frames."""

from xml_generator import seconds_to_frames


def test_seconds_to_frames_um_segundo_e_meio_a_30fps() -> None:
    assert seconds_to_frames(1.5, 30) == 45


def test_seconds_to_frames_zero() -> None:
    assert seconds_to_frames(0.0, 30) == 0


def test_seconds_to_frames_arredonda_corretamente() -> None:
    assert seconds_to_frames(1.0, 30) == 30
    assert seconds_to_frames(0.51, 30) == 15
