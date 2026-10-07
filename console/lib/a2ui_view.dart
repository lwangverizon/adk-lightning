import 'dart:convert';

import 'package:flutter/material.dart';

import 'a2ui_binding.dart';
import 'a2ui_icons.dart';
import 'a2ui_media.dart';
import 'markdown_text.dart';

/// Basic-catalog renderer for v0.9 and the pinned v1.0 candidate.
///
/// Inputs are surface snapshots from adkl.a2ui_protocol. Legacy snapshots with
/// inferred roots are also accepted. Local control edits are isolated to this
/// surface. The console inspects events; an embedding host can explicitly wire
/// [onAction] to its transport. No navigation or business tool runs implicitly.
class A2uiView extends StatefulWidget {
  const A2uiView({super.key, required this.a2ui, this.onAction});
  final Map<String, dynamic> a2ui;
  final ValueChanged<Map<String, dynamic>>? onAction;

  @override
  State<A2uiView> createState() => _A2uiViewState();
}

class _A2uiViewState extends State<A2uiView> {
  dynamic _data;
  final Map<String, dynamic> _edits = {};
  final Map<String, int> _tabs = {};
  final Map<String, String> _filters = {};
  int _revision = 0;
  VoidCallback? _refreshDialog;

  void _mutate(VoidCallback update) {
    setState(update);
    _refreshDialog?.call();
  }

  @override
  void initState() {
    super.initState();
    _reset();
  }

  void _reset() {
    _data = jsonDecode(jsonEncode(widget.a2ui['dataModel'] ?? {}));
    _edits.clear();
    _tabs.clear();
    _filters.clear();
    _revision++;
  }

  @override
  void didUpdateWidget(A2uiView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (jsonEncode(oldWidget.a2ui) != jsonEncode(widget.a2ui)) _reset();
  }

  Widget _note(String text) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 6),
    child: Text(
      text,
      style: TextStyle(color: Theme.of(context).colorScheme.error),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final surfaces = widget.a2ui['surfaces'];
    if (surfaces is List) {
      return Column(
        children: [
          for (final surface in surfaces)
            A2uiView(
              key: ValueKey(surface['surfaceId']),
              a2ui: Map<String, dynamic>.from(surface),
              onAction: widget.onAction,
            ),
        ],
      );
    }
    final version = widget.a2ui['version'] ?? 'v0.9';
    if (!const ['v0.9', 'v1.0'].contains(version)) {
      return _note('Unsupported A2UI version: $version');
    }
    final components = widget.a2ui['components'];
    if (components is! List || components.isEmpty) {
      return _note(
        (widget.a2ui['warnings'] as List? ??
                ['The converter produced no components.'])
            .join('\n'),
      );
    }
    final byId = <String, Map<String, dynamic>>{};
    for (final component in components) {
      if (component is! Map || component['id'] is! String) {
        return _note(
          'Invalid A2UI component: expected an object with a string id',
        );
      }
      byId[component['id']] = Map<String, dynamic>.from(component);
    }
    final roots = widget.a2ui['roots'];
    final ids = roots is List && roots.isNotEmpty
        ? roots
        : [widget.a2ui['root'] ?? 'root'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final id in ids) _node(byId, '$id'),
        for (final warning in widget.a2ui['warnings'] as List? ?? [])
          _note('$warning'),
      ],
    );
  }

  Widget _node(
    Map<String, Map<String, dynamic>> byId,
    String id, {
    String scope = '',
    int depth = 0,
  }) {
    if (depth > 24) return _note('component tree deeper than 24 levels at $id');
    final raw = byId[id];
    if (raw == null) return _note('unresolved component reference: $id');
    try {
      final child = _component(byId, raw, scope, depth);
      final accessibility = a2uiResolve(raw['accessibility'], _data, scope);
      if (accessibility is! Map) return child;
      return ExcludeSemantics(
        excluding: accessibility['hidden'] == true,
        child: Semantics(
          label: accessibility['label'] as String?,
          hint: accessibility['description'] as String?,
          liveRegion: const [
            'polite',
            'assertive',
          ].contains(accessibility['live']),
          child: child,
        ),
      );
    } catch (error) {
      return _note('$id: invalid or unsupported component: $error');
    }
  }

  Widget _component(
    Map<String, Map<String, dynamic>> byId,
    Map<String, dynamic> raw,
    String scope,
    int depth,
  ) {
    final id = '${raw['id']}';
    final instance = '$scope#$id';
    dynamic resolve(dynamic v) => a2uiResolve(v, _data, scope);
    String text(dynamic v) => '${resolve(v) ?? ''}';
    final c = <String, dynamic>{};
    for (final entry in raw.entries) {
      // Structural templates and action bindings must retain their paths.
      c[entry.key] = const ['children', 'action', 'checks'].contains(entry.key)
          ? entry.value
          : resolve(entry.value);
    }
    final kind = '${c['component']}';
    Widget kid(dynamic child, [String? childScope]) =>
        _node(byId, '$child', scope: childScope ?? scope, depth: depth + 1);
    List<Widget> kids() {
      final children = raw['children'];
      if (children is Map) {
        final path = '${children['path']}';
        final absolute = path.startsWith('/') ? path : '$scope/$path';
        final items = a2uiRead(_data, path, scope);
        if (items == null) return [];
        if (items is! List) {
          throw const FormatException('template path must resolve to a list');
        }
        return [
          for (var i = 0; i < items.length; i++)
            KeyedSubtree(
              key: ValueKey('$instance:$i'),
              child: kid(children['componentId'], '$absolute/$i'),
            ),
        ];
      }
      if (children != null && children is! List) {
        throw const FormatException('children must be a list or template');
      }
      return [
        if (raw['child'] != null) kid(raw['child']),
        for (final child in children as List? ?? []) kid(child),
      ];
    }

    dynamic value() =>
        _edits.containsKey(instance) ? _edits[instance] : c['value'];
    void change(dynamic v) {
      try {
        _mutate(() {
          final binding = raw['value'];
          if (binding is Map && binding['path'] is String) {
            a2uiWrite(_data, binding['path'], v, scope);
          } else {
            _edits[instance] = v;
          }
        });
      } catch (error) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Invalid binding: $error')));
      }
    }

    final label = '${c['label'] ?? ''}';
    final cs = Theme.of(context).colorScheme;
    final key = ValueKey('$instance:$_revision');
    switch (kind) {
      case 'Text':
        final content = '${c['text'] ?? c['value'] ?? ''}';
        final variant = c['variant'] ?? 'body';
        if (variant == 'body' && RegExp(r'[*#\n_]').hasMatch(content)) {
          return MarkdownText(content, fontSize: 12);
        }
        return SelectableText(
          content,
          style: TextStyle(
            fontSize: variant == 'caption'
                ? 10.5
                : '$variant'.startsWith('h')
                ? 15
                : 12,
            fontWeight: '$variant'.startsWith('h')
                ? FontWeight.bold
                : FontWeight.normal,
          ),
        );
      case 'Card':
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: kids(),
            ),
          ),
        );
      case 'Column':
      case 'Row':
        final horizontal = kind == 'Row';
        final axis = switch (c['align']) {
          'center' => CrossAxisAlignment.center,
          'end' => CrossAxisAlignment.end,
          'start' => CrossAxisAlignment.start,
          _ => CrossAxisAlignment.stretch,
        };
        final main = switch (c['justify']) {
          'center' => MainAxisAlignment.center,
          'end' => MainAxisAlignment.end,
          'spaceBetween' => MainAxisAlignment.spaceBetween,
          'spaceAround' => MainAxisAlignment.spaceAround,
          'spaceEvenly' => MainAxisAlignment.spaceEvenly,
          _ => MainAxisAlignment.start,
        };
        final children = kids();
        if (!horizontal) {
          return Column(
            mainAxisSize: MainAxisSize.min,
            mainAxisAlignment: main,
            crossAxisAlignment: axis,
            children: children,
          );
        }
        final refs = raw['children'];
        final row = Row(
          mainAxisAlignment: main,
          crossAxisAlignment: axis,
          children: [
            for (var i = 0; i < children.length; i++)
              Flexible(
                fit:
                    c['justify'] == 'stretch' ||
                        (refs is List &&
                            ((byId['${refs[i]}']?['weight'] as num?) ?? 0) > 0)
                    ? FlexFit.tight
                    : FlexFit.loose,
                flex: refs is List
                    ? (((byId['${refs[i]}']?['weight'] as num?) ?? 1) * 100)
                          .round()
                          .clamp(1, 100000)
                    : 100,
                child: children[i],
              ),
          ],
        );
        return axis == CrossAxisAlignment.stretch
            ? IntrinsicHeight(child: row)
            : row;
      case 'List':
        final align = switch (c['align']) {
          'center' => CrossAxisAlignment.center,
          'end' => CrossAxisAlignment.end,
          'start' => CrossAxisAlignment.start,
          _ => CrossAxisAlignment.stretch,
        };
        return c['direction'] == 'horizontal'
            ? SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final w in kids()) SizedBox(width: 240, child: w),
                  ],
                ),
              )
            : Column(crossAxisAlignment: align, children: kids());
      case 'Divider':
        return c['axis'] == 'vertical'
            ? const SizedBox(height: 32, child: VerticalDivider())
            : const Divider();
      case 'Icon':
        final name = c['name'];
        if (name is Map && name['svgPath'] is String) {
          return a2uiSvg(name['svgPath']);
        }
        final icon = a2uiIcons[name];
        return icon == null
            ? _note('Unknown icon: $name')
            : Icon(icon, semanticLabel: '$name');
      case 'Image':
        final url = _url(c['url']);
        final size = switch (c['variant']) {
          'icon' => 24.0,
          'avatar' => 48.0,
          'smallFeature' => 80.0,
          'largeFeature' || 'header' => 240.0,
          _ => 160.0,
        };
        final fit = switch (c['fit']) {
          'contain' => BoxFit.contain,
          'cover' => BoxFit.cover,
          'none' => BoxFit.none,
          'scaleDown' => BoxFit.scaleDown,
          _ => BoxFit.fill,
        };
        final image = Image.network(
          url,
          height: size,
          fit: fit,
          semanticLabel: c['description'] as String?,
          errorBuilder: (_, error, stack) => _note('Image unavailable: $url'),
        );
        if (const ['avatar', 'icon'].contains(c['variant'])) {
          return Align(
            alignment: Alignment.centerLeft,
            child: SizedBox(
              width: size,
              height: size,
              child: c['variant'] == 'avatar' ? ClipOval(child: image) : image,
            ),
          );
        }
        return image;
      case 'Video':
      case 'AudioPlayer':
        return a2uiMedia(
          kind,
          _url(c['url']),
          poster: c['posterUrl'] == null ? null : _url(c['posterUrl']),
          description: c['description'] as String?,
        );
      case 'Tabs':
        final tabs = c['tabs'] as List;
        if (tabs.isEmpty) return _note('Tabs requires at least one tab');
        final selected = (_tabs[instance] ?? 0).clamp(0, tabs.length - 1);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Wrap(
              spacing: 8,
              children: [
                for (var i = 0; i < tabs.length; i++)
                  ChoiceChip(
                    label: Text('${tabs[i]['title']}'),
                    selected: i == selected,
                    onSelected: (_) => _mutate(() => _tabs[instance] = i),
                  ),
              ],
            ),
            kid(tabs[selected]['child']),
          ],
        );
      case 'Modal':
        return Semantics(
          button: true,
          label: 'Open dialog',
          child: InkWell(
            onTap: () => showDialog<void>(
              context: context,
              builder: (dialogContext) => StatefulBuilder(
                builder: (ctx, refresh) {
                  _refreshDialog = () => refresh(() {});
                  return AlertDialog(
                    content: SizedBox(
                      width: 420,
                      child: SingleChildScrollView(child: kid(raw['content'])),
                    ),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.pop(dialogContext),
                        child: const Text('Close'),
                      ),
                    ],
                  );
                },
              ),
            ).whenComplete(() => _refreshDialog = null),
            child: AbsorbPointer(child: kid(raw['trigger'])),
          ),
        );
      case 'Button':
        final action = raw['action'] as Map? ?? {};
        final event = action['event'] as Map?;
        final name = event?['name'];
        void press() {
          try {
            final resolved = Map<String, dynamic>.from(resolve(action) as Map);
            if (resolved.containsKey('functionCall')) {
              throw const FormatException(
                'Local function actions are not implemented',
              );
            }
            final event = resolved['event'];
            if (event is! Map ||
                event['name'] is! String ||
                (event['name'] as String).isEmpty) {
              throw const FormatException('Button requires a named event');
            }
            final envelope = <String, dynamic>{
              'version': widget.a2ui['version'] ?? 'v0.9',
              'surfaceId': widget.a2ui['surfaceId'],
              'sourceComponentId': id,
              'event': resolved['event'],
            };
            if (widget.onAction != null) {
              widget.onAction!(envelope);
            } else {
              showDialog<void>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Action preview — not sent'),
                  content: SingleChildScrollView(
                    child: SelectableText(
                      const JsonEncoder.withIndent('  ').convert(envelope),
                    ),
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('Close'),
                    ),
                  ],
                ),
              );
            }
          } catch (error) {
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(SnackBar(content: Text('$error')));
          }
        }
        final child = IgnorePointer(child: kid(raw['child']));
        final button = switch (c['variant']) {
          'primary' => FilledButton(onPressed: press, child: child),
          'borderless' => TextButton(onPressed: press, child: child),
          _ => OutlinedButton(onPressed: press, child: child),
        };
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            button,
            if (name != null)
              Text(
                '$name',
                style: TextStyle(fontSize: 9, color: cs.onSurfaceVariant),
              ),
          ],
        );
      case 'TextField':
        final validation = c['validationRegexp'] is String
            ? RegExp(c['validationRegexp'])
            : null;
        return _BoundInput(
          key: key,
          value: '${value() ?? ''}',
          decoration: InputDecoration(
            labelText: label,
            hintText: c['placeholder'] as String?,
          ),
          obscureText: c['variant'] == 'obscured',
          maxLines: c['variant'] == 'longText' ? null : 1,
          keyboardType: c['variant'] == 'number'
              ? TextInputType.number
              : TextInputType.text,
          validator: (v) => validation != null && !validation.hasMatch(v ?? '')
              ? 'Invalid value'
              : null,
          onChanged: change,
        );
      case 'CheckBox':
        return CheckboxListTile(
          title: Text(label),
          value: value() == true,
          controlAffinity: ListTileControlAffinity.leading,
          onChanged: change,
        );
      case 'Slider':
        final min = (c['min'] as num? ?? 0).toDouble();
        final max = (c['max'] as num).toDouble();
        if (!min.isFinite || !max.isFinite || min >= max) {
          return _note('Invalid slider range');
        }
        final current = (value() as num? ?? min).toDouble().clamp(min, max);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('$label: $current'),
            Slider(
              value: current,
              min: min,
              max: max,
              divisions: c['steps'] is int && c['steps'] > 0
                  ? c['steps']
                  : null,
              label: '$current',
              onChanged: change,
            ),
          ],
        );
      case 'ChoicePicker':
        final options = c['options'] as List;
        final selected = value() as List? ?? [];
        final multiple = c['variant'] == 'multipleSelection';
        final filter = (_filters[instance] ?? '').toLowerCase();
        void select(dynamic v, bool checked) => change(
          multiple
              ? (checked
                    ? {...selected, v}.toList()
                    : selected.where((x) => x != v).toList())
              : (checked ? [v] : <String>[]),
        );
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (label.isNotEmpty) Text(label),
            if (c['filterable'] == true)
              TextField(
                decoration: const InputDecoration(labelText: 'Filter options'),
                onChanged: (v) => _mutate(() => _filters[instance] = v),
              ),
            Wrap(
              children: [
                for (final option in options)
                  if ('${option['label']}'.toLowerCase().contains(filter))
                    c['displayStyle'] == 'chips'
                        ? FilterChip(
                            label: Text('${option['label']}'),
                            selected: selected.contains(option['value']),
                            onSelected: (checked) =>
                                select(option['value'], checked),
                          )
                        : CheckboxListTile(
                            title: Text('${option['label']}'),
                            value: selected.contains(option['value']),
                            onChanged: (checked) =>
                                select(option['value'], checked ?? false),
                          ),
              ],
            ),
          ],
        );
      case 'DateTimeInput':
        return _BoundInput(
          key: key,
          value: '${value() ?? ''}',
          decoration: InputDecoration(
            labelText: label,
            hintText: c['enableDate'] == true
                ? (c['enableTime'] == true
                      ? 'YYYY-MM-DDTHH:mm:ss'
                      : 'YYYY-MM-DD')
                : 'HH:mm:ss',
            suffixIcon: IconButton(
              tooltip: 'Choose date/time',
              icon: const Icon(Icons.calendar_today),
              onPressed: () async {
                var selected =
                    DateTime.tryParse(
                      c['enableDate'] == true
                          ? '${value()}'
                          : '2000-01-01T${value()}',
                    ) ??
                    DateTime.now();
                if (c['enableDate'] == true) {
                  final first =
                      DateTime.tryParse('${c['min']}') ?? DateTime(1900);
                  final last =
                      DateTime.tryParse('${c['max']}') ?? DateTime(2100);
                  if (first.isAfter(last)) return;
                  if (selected.isBefore(first)) selected = first;
                  if (selected.isAfter(last)) selected = last;
                  final day = await showDatePicker(
                    context: context,
                    initialDate: selected,
                    firstDate: first,
                    lastDate: last,
                  );
                  if (day == null || !mounted) return;
                  selected = day;
                }
                if (c['enableTime'] == true) {
                  final time = await showTimePicker(
                    context: context,
                    initialTime: TimeOfDay.fromDateTime(selected),
                  );
                  if (time == null || !mounted) return;
                  selected = DateTime(
                    selected.year,
                    selected.month,
                    selected.day,
                    time.hour,
                    time.minute,
                  );
                }
                final iso = selected.toIso8601String().split('.').first;
                change(
                  c['enableDate'] == true
                      ? (c['enableTime'] == true ? iso : iso.split('T').first)
                      : iso.split('T').last,
                );
              },
            ),
          ),
          enabled: c['enableDate'] == true || c['enableTime'] == true,
          validator: (v) {
            if (v == null || v.isEmpty) return null;
            final dateEnabled = c['enableDate'] == true;
            final timeEnabled = c['enableTime'] == true;
            final datePattern = r'\d{4}-\d{2}-\d{2}';
            final timePattern =
                r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?';
            final pattern = dateEnabled
                ? '$datePattern${timeEnabled ? 'T$timePattern' : ''}'
                : timePattern;
            if (!RegExp('^$pattern\$').hasMatch(v)) {
              return 'Enter an ISO 8601 date/time';
            }
            if (dateEnabled) {
              final pieces = v
                  .substring(0, 10)
                  .split('-')
                  .map(int.parse)
                  .toList();
              final day = DateTime(pieces[0], pieces[1], pieces[2]);
              if (day.month != pieces[1] || day.day != pieces[2]) {
                return 'Enter a valid calendar date';
              }
            }
            DateTime? parse(String s) =>
                DateTime.tryParse(dateEnabled ? s : '2000-01-01T$s');
            final date = parse(v);
            if (date == null) return 'Enter an ISO 8601 date/time';
            final min = c['min'] == null ? null : parse('${c['min']}');
            final max = c['max'] == null ? null : parse('${c['max']}');
            if (min != null && date.isBefore(min)) return 'Before minimum';
            if (max != null && date.isAfter(max)) return 'After maximum';
            return null;
          },
          onChanged: change,
        );
      case 'Badge':
        return Align(
          alignment: Alignment.centerLeft,
          child: Chip(label: Text(text(raw['text'] ?? raw['value']))),
        );
      default:
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(kind),
                const Text('client component'),
                const Text(
                  'Rendered by the client, not reproduced here. Its data:',
                ),
                for (final field in c.entries)
                  if (!const {
                    'id',
                    'component',
                    'child',
                    'children',
                  }.contains(field.key))
                    Text('${field.key}: ${_short(field.value)}'),
                ...kids(),
              ],
            ),
          ),
        );
    }
  }

  String _short(dynamic value) {
    if (value is List) {
      return value.isEmpty
          ? '[]'
          : '${value.length} item${value.length == 1 ? '' : 's'}';
    }
    if (value is Map) {
      return value.isEmpty ? '{}' : '{${value.keys.take(4).join(', ')}}';
    }
    final text = '$value'.replaceAll('\n', ' ');
    return text.length > 70 ? '${text.substring(0, 70)}…' : text;
  }

  String _url(dynamic value) {
    final url = Uri.tryParse('$value');
    if (url == null ||
        !const ['https', 'http'].contains(url.scheme) ||
        url.host.isEmpty ||
        url.userInfo.isNotEmpty) {
      throw const FormatException(
        'Media requires an HTTP(S) URL without credentials',
      );
    }
    return url.toString();
  }
}

/// Keep cursor/edit state while reflecting changes to the same binding elsewhere.
class _BoundInput extends StatefulWidget {
  const _BoundInput({
    super.key,
    required this.value,
    required this.decoration,
    required this.onChanged,
    this.obscureText = false,
    this.maxLines = 1,
    this.keyboardType,
    this.validator,
    this.enabled = true,
  });
  final String value;
  final InputDecoration decoration;
  final ValueChanged<String> onChanged;
  final bool obscureText;
  final int? maxLines;
  final TextInputType? keyboardType;
  final FormFieldValidator<String>? validator;
  final bool enabled;
  @override
  State<_BoundInput> createState() => _BoundInputState();
}

class _BoundInputState extends State<_BoundInput> {
  late final controller = TextEditingController(text: widget.value);
  @override
  void didUpdateWidget(_BoundInput oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (controller.text != widget.value) {
      controller.value = TextEditingValue(
        text: widget.value,
        selection: TextSelection.collapsed(offset: widget.value.length),
      );
    }
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => TextFormField(
    controller: controller,
    decoration: widget.decoration,
    onChanged: widget.onChanged,
    obscureText: widget.obscureText,
    maxLines: widget.maxLines,
    keyboardType: widget.keyboardType,
    validator: widget.validator,
    enabled: widget.enabled,
    autovalidateMode: AutovalidateMode.onUserInteraction,
  );
}
