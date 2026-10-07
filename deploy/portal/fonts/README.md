# Same-origin portal fallback fonts

Roboto and Noto Sans Symbols are redistributed with their accompanying licenses.
These exact bytes were independently retrieved from the public Google Fonts
versioned endpoints below and compared with the bundled files. No private
application or registry is needed to obtain them. Preserve filenames: Flutter
requests these paths.

| File / public upstream URL | SHA-256 |
| --- | --- |
| [Roboto](https://fonts.gstatic.com/s/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2) | `35b02ca266b79eb4996590f15817425a1ce9ebf48f84471843233ff614656bf2` |
| [Noto Sans Symbols](https://fonts.gstatic.com/s/notosanssymbols/v43/rP2up3q65FkAtHfwd-eIS2brbDN6gxP34F9jRRCe4W3gfQ8gb_VFRkzrbQ.woff2) | `08202e258ea583254c036cff46a7077bb5af4f82c41a6c0a6775f6e44d99f1aa` |

The dedicated portal bootstrap points fallback requests at this directory, not
a third-party CDN. This is not universal Unicode coverage. Hosts publishing CJK,
emoji or other scripts must provision and license their needed fallback subsets
and verify the rendered publication. Missing local glyph coverage must not be
worked around by widening CSP or enabling external font requests.
