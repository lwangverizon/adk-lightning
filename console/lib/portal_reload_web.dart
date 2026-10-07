import 'package:web/web.dart' as web;

/// User initiated only; never automatically loop during a rolling deployment.
void reload() => web.window.location.reload();
