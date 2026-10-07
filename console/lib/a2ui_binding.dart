/// JSON Pointer bindings shared by controls, templates and action inspection.
dynamic a2uiRead(dynamic model, String path, [String scope = '']) {
  final absolute = path.startsWith('/') ? path : '$scope/$path';
  if (absolute == '/' || absolute.isEmpty) return model;
  dynamic value = model;
  for (final part in absolute.substring(1).split('/')) {
    final key = part.replaceAll('~1', '/').replaceAll('~0', '~');
    if (value is Map) {
      value = value[key];
    } else if (value is List) {
      final index = int.tryParse(key);
      value = index != null && index >= 0 && index < value.length
          ? value[index]
          : null;
    } else {
      return null;
    }
  }
  return value;
}

dynamic a2uiResolve(dynamic value, dynamic model, [String scope = '']) {
  if (value is Map) {
    if (value.length == 1 && value['path'] is String) {
      return a2uiRead(model, '${value['path']}', scope);
    }
    if (value.containsKey('call')) {
      throw FormatException('Unsupported catalog function: ${value['call']}');
    }
    return value.map((k, v) => MapEntry('$k', a2uiResolve(v, model, scope)));
  }
  if (value is List) {
    return value.map((v) => a2uiResolve(v, model, scope)).toList();
  }
  return value;
}

void a2uiWrite(dynamic model, String path, dynamic value, [String scope = '']) {
  final absolute = path.startsWith('/') ? path : '$scope/$path';
  final parts = absolute
      .substring(1)
      .split('/')
      .map((p) => p.replaceAll('~1', '/').replaceAll('~0', '~'))
      .toList();
  dynamic target = model;
  for (final p in parts.take(parts.length - 1)) {
    if (target is Map) {
      target = target.putIfAbsent(p, () => <String, dynamic>{});
    } else if (target is List) {
      target = target[int.parse(p)];
    } else {
      throw const FormatException('Binding traverses a scalar');
    }
  }
  if (target is Map) {
    target[parts.last] = value;
  } else if (target is List) {
    target[int.parse(parts.last)] = value;
  } else {
    throw const FormatException('Binding traverses a scalar');
  }
}
