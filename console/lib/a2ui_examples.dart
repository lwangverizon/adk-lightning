/// Small, credential-free examples used by the gallery and renderer tests.
const a2uiExamples = <String, Map<String, dynamic>>{
  'Text': {'text': '**A2UI** renders structured content.'},
  'Image': {
    'url':
        'https://flutter.github.io/assets-for-api-docs/assets/widgets/owl-2.jpg',
    'description': 'Example image',
    'fit': 'contain',
  },
  'Icon': {'name': 'home'},
  'Video': {
    'url':
        'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4',
  },
  'AudioPlayer': {
    'url':
        'https://interactive-examples.mdn.mozilla.net/media/cc0-audio/t-rex-roar.mp3',
    'description': 'Example audio',
  },
  'Row': {
    'children': ['first', 'second'],
    'align': 'center',
    'justify': 'spaceBetween',
  },
  'Column': {
    'children': ['first', 'second'],
  },
  'List': {
    'children': {'componentId': 'item', 'path': '/items'},
  },
  'Card': {'child': 'first'},
  'Tabs': {
    'tabs': [
      {'title': 'First tab', 'child': 'first'},
      {'title': 'Second tab', 'child': 'second'},
    ],
  },
  'Modal': {'trigger': 'first', 'content': 'modal-body'},
  'Divider': {'axis': 'horizontal'},
  'Button': {
    'child': 'first',
    'variant': 'primary',
    'action': {
      'event': {
        'name': 'example',
        'context': {
          'name': {'path': '/name'},
        },
      },
    },
  },
  'TextField': {
    'label': 'Name',
    'value': {'path': '/name'},
    'variant': 'shortText',
  },
  'CheckBox': {
    'label': 'Subscribe',
    'value': {'path': '/subscribed'},
  },
  'ChoicePicker': {
    'label': 'Topics',
    'variant': 'multipleSelection',
    'value': {'path': '/topics'},
    'filterable': true,
    'displayStyle': 'chips',
    'options': [
      {'label': 'Math', 'value': 'math'},
      {'label': 'Science', 'value': 'science'},
    ],
  },
  'Slider': {
    'label': 'Volume',
    'min': 0,
    'max': 100,
    'value': {'path': '/volume'},
  },
  'DateTimeInput': {
    'label': 'Appointment',
    'enableDate': true,
    'enableTime': true,
    'value': {'path': '/appointment'},
    'min': '2026-01-01',
    'max': '2027-12-31',
  },
};

Map<String, dynamic> a2uiExample(String kind, String version) => {
  'version': version,
  'surfaceId': '$version-$kind',
  'catalogId':
      'https://a2ui.org/specification/${version == 'v1.0' ? 'v1_0' : 'v0_9'}/catalogs/basic/catalog.json',
  'root': 'root',
  'dataModel': {
    'name': 'Ada',
    'subscribed': false,
    'topics': <String>[],
    'volume': 25,
    'appointment': '2026-09-06T10:00:00',
    'items': [
      {'name': 'First item'},
      {'name': 'Second item'},
    ],
  },
  'components': [
    {
      'id': 'root',
      'component': kind,
      ...a2uiExamples[kind]!,
      if (version == 'v1.0' && kind == 'Slider') 'steps': 10,
      if (version == 'v1.0' && kind == 'TextField')
        'placeholder': 'Enter your name',
    },
    {'id': 'first', 'component': 'Text', 'text': 'First content'},
    {'id': 'second', 'component': 'Text', 'text': 'Second content'},
    {
      'id': 'item',
      'component': 'Text',
      'text': {'path': 'name'},
    },
    {
      'id': 'modal-body',
      'component': 'Column',
      'children': ['modal-check', 'modal-value'],
    },
    {
      'id': 'modal-check',
      'component': 'CheckBox',
      'label': 'Modal subscription',
      'value': {'path': '/subscribed'},
    },
    {
      'id': 'modal-value',
      'component': 'Text',
      'text': {'path': '/subscribed'},
    },
  ],
};
