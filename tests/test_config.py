from pathlib import Path

from core.config import (
    DEFAULT_MODEL_NAME,
    DEFAULT_MODELS_DIR,
    Config,
    EmbeddingConfig,
    load_config,
)


def test_defaults_when_file_missing(tmp_path: Path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.embedding.model_name == DEFAULT_MODEL_NAME
    assert cfg.embedding.models_dir == DEFAULT_MODELS_DIR


def test_defaults_when_embedding_section_missing(tmp_path: Path):
    f = tmp_path / "config.toml"
    f.write_text("# nothing relevant here\n[other]\nkey = 'value'\n")
    cfg = load_config(f)
    assert cfg.embedding == EmbeddingConfig()


def test_reads_custom_model_name(tmp_path: Path):
    f = tmp_path / "config.toml"
    f.write_text('[embedding]\nmodel_name = "bge-m3"\n')
    cfg = load_config(f)
    assert cfg.embedding.model_name == "bge-m3"
    assert cfg.embedding.models_dir == DEFAULT_MODELS_DIR


def test_expands_tilde_in_models_dir(tmp_path: Path):
    f = tmp_path / "config.toml"
    f.write_text('[embedding]\nmodels_dir = "~/custom/models"\n')
    cfg = load_config(f)
    assert cfg.embedding.models_dir == Path.home() / "custom" / "models"


def test_model_dir_combines_dir_and_name(tmp_path: Path):
    f = tmp_path / "config.toml"
    f.write_text(
        '[embedding]\nmodel_name = "x"\nmodels_dir = "/opt/marbles-models"\n'
    )
    cfg = load_config(f)
    assert cfg.embedding.model_dir == Path("/opt/marbles-models/x")


def test_unknown_keys_are_ignored(tmp_path: Path):
    f = tmp_path / "config.toml"
    f.write_text('[embedding]\nmodel_name = "x"\nfuture_field = 42\n')
    cfg = load_config(f)  # must not raise
    assert cfg.embedding.model_name == "x"


def test_config_default_dataclass():
    cfg = Config()
    assert cfg.embedding.model_name == DEFAULT_MODEL_NAME
