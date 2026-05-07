import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:path_provider/path_provider.dart';
import 'package:logger/logger.dart';
import 'api_service.dart';

final _logger = Logger();

class AudioService {
  final AudioRecorder _recorder = AudioRecorder();
  final AudioPlayer _player = AudioPlayer();
  bool _isRecording = false;
  String? _currentRecordingPath;

  bool get isRecording => _isRecording;

  /// Request microphone permission and start recording to a temp WAV file
  Future<void> startRecording() async {
    final status = await Permission.microphone.request();
    if (!status.isGranted) {
      throw Exception('Microphone permission denied. Please enable it in settings.');
    }

    final tempDir = await getTemporaryDirectory();
    _currentRecordingPath =
        '${tempDir.path}/sahayak_recording_${DateTime.now().millisecondsSinceEpoch}.wav';

    const config = RecordConfig(
      encoder: AudioEncoder.wav,
      sampleRate: 16000,
      numChannels: 1,
      bitRate: 256000,
    );

    await _recorder.start(config, path: _currentRecordingPath!);
    _isRecording = true;
    _logger.d('Recording started: $_currentRecordingPath');
  }

  /// Stop recording and return the audio bytes
  Future<Uint8List> stopRecording() async {
    if (!_isRecording) {
      throw Exception('No active recording to stop.');
    }

    final path = await _recorder.stop();
    _isRecording = false;
    _logger.d('Recording stopped: $path');

    if (path == null) {
      throw Exception('Recording path is null — recording may have failed.');
    }

    final file = File(path);
    if (!file.existsSync()) {
      throw Exception('Recorded file not found at path: $path');
    }

    final bytes = await file.readAsBytes();
    _logger.d('Recording bytes: ${bytes.length}');

    // Clean up temp file
    try {
      await file.delete();
    } catch (e) {
      _logger.w('Could not delete temp recording: $e');
    }

    return bytes;
  }

  /// Play raw audio bytes (WAV)
  Future<void> playAudio(Uint8List audioBytes) async {
    try {
      await _player.stop();
      final source = BytesSource(audioBytes);
      await _player.play(source);
      _logger.d('Playing audio: ${audioBytes.length} bytes');
    } catch (e) {
      _logger.e('Error playing audio: $e');
      rethrow;
    }
  }

  /// Fetch TTS audio from the API and play it
  Future<void> playText(String text, ApiService api) async {
    try {
      final audioBytes = await api.textToSpeech(text);
      await playAudio(audioBytes);
    } catch (e) {
      _logger.e('TTS failed: $e');
      rethrow;
    }
  }

  /// Stop current playback
  Future<void> stopPlayback() async {
    await _player.stop();
  }

  /// Whether audio is currently playing
  Future<bool> get isPlaying async {
    return _player.state == PlayerState.playing;
  }

  void dispose() {
    _recorder.dispose();
    _player.dispose();
  }
}

final audioServiceProvider = Provider<AudioService>((ref) {
  final service = AudioService();
  ref.onDispose(service.dispose);
  return service;
});
