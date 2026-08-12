"""Sphinx configuration for the ephys-acquisition documentation.

Build locally with::

    uv run --group docs sphinx-build -b html docs docs/_build/html

or with live reload while writing::

    uv run --group docs sphinx-autobuild docs docs/_build/html --open-browser

The API reference is produced by ``sphinx-autoapi``, which parses the source
statically.  Nothing in this repository is imported to build the docs, so no
DAQ driver, camera SDK, or Qt runtime is needed on the build machine.
"""

from __future__ import annotations

import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# -- Project information -----------------------------------------------------

project = "Ephys Acquisition"
author = "Kyle Thieringer"
copyright = f"{datetime.date.today():%Y}, {author}"
release = "0.1.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",
    "autoapi.extension",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_design",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- MyST (Markdown) ---------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",      # ::: fenced directives
    "deflist",          # definition lists
    "fieldlist",
    "attrs_inline",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

# -- AutoAPI -----------------------------------------------------------------
# Static source parsing: no imports, no hardware dependencies at build time.

autoapi_type = "python"
autoapi_dirs = [str(REPO_ROOT)]
autoapi_root = "autoapi"
autoapi_keep_files = False

# Must stay True: sphinx-autoapi only generates the top-level ``autoapi/index``
# page when this is set (see autoapi/_mapper.py::_output_top_rst).  Because
# index.md already lists ``autoapi/index`` in a toctree explicitly, AutoAPI
# detects the existing entry and does not inject a second one.
autoapi_add_toctree_entry = True

autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
]
autoapi_python_class_content = "both"   # class docstring + __init__ docstring
autoapi_member_order = "groupwise"

# Everything that is not first-party source, plus work-in-progress modules.
autoapi_ignore = [
    "*/.venv/*",
    "*/__pycache__/*",
    "*/.git/*",
    "*/.idea/*",
    "*/.claude/*",
    "*/docs/*",
    "*/logs/*",
    "*/assets/*",
    "*/*.egg-info/*",
    "*/conftest.py",
    # work in progress — excluded deliberately, see docs/reference/code-map.md
    "*/_head_sf.py",
    "*/align_video_skeleton.py",
]

# -- Napoleon (docstring styles) ---------------------------------------------
# The codebase mixes reStructuredText-flavoured docstrings with Google-style
# "Returns:" sections, so both parsers stay on.

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_examples = True

# Render "Attributes:" blocks as :ivar: fields inside the class body instead of
# separate attribute directives.  Without this, every documented dataclass
# field is registered twice — once by Napoleon and once by AutoAPI — which
# Sphinx reports as a duplicate object description.
napoleon_use_ivar = True

# Section headings this codebase uses that Napoleon does not know natively.
# "Signals:" documents Qt signals on the worker and panel classes; left
# unregistered its indented body parses as a block quote and errors out.
napoleon_custom_sections = [
    ("Signals", "params_style"),
]

# -- Intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
    "scipy": ("https://docs.scipy.org/doc/scipy", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
    "matplotlib": ("https://matplotlib.org/stable", None),
    "h5py": ("https://docs.h5py.org/en/stable", None),
}
intersphinx_timeout = 10

# -- HTML output -------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_favicon = "_static/favicon.ico"
html_title = "Ephys Acquisition"
html_show_sourcelink = False

html_theme_options = {
    "logo": {
        "text": "Ephys Acquisition",
        "image_light": "_static/logo.png",
        "image_dark": "_static/logo.png",
    },
    "github_url": "https://github.com/kylethieringer/ephys-acquisition",
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "show_nav_level": 1,
    "navigation_depth": 3,
    "header_links_before_dropdown": 5,
    "navbar_align": "left",
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "back_to_top_button": True,
    "pygments_light_style": "friendly",
    "pygments_dark_style": "github-dark",
    "footer_start": ["copyright"],
    "footer_end": ["theme-version"],
}

html_context = {
    "github_user": "kylethieringer",
    "github_repo": "ephys-acquisition",
    "github_version": "main",
    "doc_path": "docs",
    "default_mode": "auto",
}

# -- sphinx-copybutton -------------------------------------------------------

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True


# -- Hooks -------------------------------------------------------------------


def _skip_undocumented_module_data(app, what, name, obj, skip, options):
    """Drop module-level variables that carry no docstring.

    Script-style modules (the Streamlit dashboard in particular) reassign
    working variables such as ``df`` at module scope repeatedly.  AutoAPI
    documents every assignment, producing duplicate entries that are noise.
    Documented module constants — everything in :mod:`config`, for example —
    are kept, because a docstring is the signal that a value is part of the
    module's public surface.
    """
    if what == "data" and not getattr(obj, "docstring", "").strip():
        return True
    return skip


def setup(app):
    app.connect("autoapi-skip-member", _skip_undocumented_module_data)
