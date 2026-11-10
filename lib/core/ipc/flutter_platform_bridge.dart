/// Flutter runtime MethodChannel & EventChannel wiring for Android NativeBridgePlugin.
/// Kept separate from pure-Dart `native_bridge.dart` so headless Dart tests run without `dart:ui`.
library;

import 'dart:async';
import 'package:flutter/services.dart';
import 'event_channels.dart';
import 'native_bridge.dart';

const MethodChannel _kNativeMethodChannel = MethodChannel('hemory/native_bridge');
const EventChannel _kStateEventChannel = EventChannel('hemory/recording/state');
const EventChannel _kLifecycleEventChannel = EventChannel('hemory/recording/events');
const EventChannel _kMeteringEventChannel = EventChannel('hemory/recording/metering');
const EventChannel _kSegmentsEventChannel = EventChannel('hemory/recording/segments');

INativeBridge createFlutterNativeBridge() {
  return NativeBridgeChannel(
    invoker: (String method, [dynamic arguments]) async {
      try {
        return await _kNativeMethodChannel.invokeMethod<dynamic>(method, arguments);
      } on MissingPluginException {
        return null;
      }
    },
  );
}

Stream<T> _safeBroadcastEventStream<T>(
  EventChannel channel,
  T Function(Map<dynamic, dynamic>) mapper,
) {
  StreamController<T>? controller;
  StreamSubscription<dynamic>? sub;

  controller = StreamController<T>.broadcast(
    onListen: () {
      try {
        sub = channel.receiveBroadcastStream().listen(
          (dynamic raw) {
            if (raw is Map && controller != null && !controller.isClosed) {
              controller.add(mapper(raw));
            }
          },
          onError: (_) {},
        );
      } on MissingPluginException {
        // Headless / widget test environment without Android plugin attached.
      } catch (_) {}
    },
    onCancel: () {
      sub?.cancel();
      sub = null;
    },
  );

  return controller.stream;
}

IRecordingEventStreams createFlutterRecordingEventStreams() {
  return RecordingEventStreams(
    stateStream: _safeBroadcastEventStream(
      _kStateEventChannel,
      RecordingStateSnapshot.fromMap,
    ),
    eventsStream: _safeBroadcastEventStream(
      _kLifecycleEventChannel,
      RecordingLifecycleEvent.fromMap,
    ),
    meteringStream: _safeBroadcastEventStream(
      _kMeteringEventChannel,
      MeteringSnapshot.fromMap,
    ),
    segmentsStream: _safeBroadcastEventStream(
      _kSegmentsEventChannel,
      SegmentCompletedEvent.fromMap,
    ),
  );
}
