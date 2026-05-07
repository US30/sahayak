import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:logger/logger.dart';
import 'api_service.dart';

final _logger = Logger();

class WebSocketService {
  WebSocketChannel? _channel;
  final _messageController = StreamController<Map<String, dynamic>>.broadcast();
  bool _isConnected = false;
  String? _currentUserId;
  String? _baseWsUrl;
  int _reconnectAttempts = 0;
  static const int _maxReconnectAttempts = 8;
  Timer? _reconnectTimer;
  bool _intentionalDisconnect = false;

  Stream<Map<String, dynamic>> get messages => _messageController.stream;
  bool get isConnected => _isConnected;

  Future<void> connect(String userId, {String? baseUrl}) async {
    _currentUserId = userId;
    _intentionalDisconnect = false;

    // Derive WS URL from HTTP base URL
    final httpBase = baseUrl ?? 'http://10.0.2.2:8000';
    _baseWsUrl = httpBase
        .replaceFirst('https://', 'wss://')
        .replaceFirst('http://', 'ws://');

    await _doConnect();
  }

  Future<void> _doConnect() async {
    if (_currentUserId == null || _baseWsUrl == null) return;

    final wsUrl = '$_baseWsUrl/ws/$_currentUserId';
    _logger.d('WebSocket connecting to $wsUrl');

    try {
      _channel = WebSocketChannel.connect(Uri.parse(wsUrl));
      await _channel!.ready;
      _isConnected = true;
      _reconnectAttempts = 0;
      _logger.d('WebSocket connected');

      _channel!.stream.listen(
        _onMessage,
        onError: _onError,
        onDone: _onDone,
        cancelOnError: false,
      );
    } catch (e) {
      _logger.e('WebSocket connection error: $e');
      _isConnected = false;
      _scheduleReconnect();
    }
  }

  void _onMessage(dynamic rawData) {
    try {
      if (rawData is String) {
        final decoded = jsonDecode(rawData);
        if (decoded is Map<String, dynamic>) {
          _messageController.add(decoded);
        }
      } else if (rawData is List<int>) {
        // Binary data — could be audio; wrap it
        _messageController.add({'type': 'binary', 'data': rawData});
      }
    } catch (e) {
      _logger.w('Failed to parse WebSocket message: $e');
    }
  }

  void _onError(Object error) {
    _logger.e('WebSocket error: $error');
    _isConnected = false;
    if (!_intentionalDisconnect) {
      _scheduleReconnect();
    }
  }

  void _onDone() {
    _logger.d('WebSocket connection closed');
    _isConnected = false;
    if (!_intentionalDisconnect) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    if (_intentionalDisconnect) return;
    if (_reconnectAttempts >= _maxReconnectAttempts) {
      _logger.w('Max WebSocket reconnect attempts reached');
      return;
    }

    // Exponential backoff: 1s, 2s, 4s, 8s, 16s …
    final delaySeconds = 1 << _reconnectAttempts;
    _reconnectAttempts++;
    _logger.d('Reconnecting WebSocket in ${delaySeconds}s (attempt $_reconnectAttempts)');

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), () {
      if (!_intentionalDisconnect) {
        _doConnect();
      }
    });
  }

  /// Send binary audio chunk over the WebSocket
  void sendAudioChunk(Uint8List bytes) {
    if (!_isConnected || _channel == null) {
      _logger.w('WebSocket not connected — cannot send audio chunk');
      return;
    }
    try {
      _channel!.sink.add(bytes);
    } catch (e) {
      _logger.e('Error sending audio chunk: $e');
    }
  }

  /// Send a JSON message
  void sendJson(Map<String, dynamic> data) {
    if (!_isConnected || _channel == null) {
      _logger.w('WebSocket not connected — cannot send JSON');
      return;
    }
    try {
      _channel!.sink.add(jsonEncode(data));
    } catch (e) {
      _logger.e('Error sending JSON: $e');
    }
  }

  void disconnect() {
    _intentionalDisconnect = true;
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _isConnected = false;
    _logger.d('WebSocket disconnected intentionally');
  }

  void dispose() {
    disconnect();
    _messageController.close();
  }
}

final webSocketServiceProvider = Provider<WebSocketService>((ref) {
  final service = WebSocketService();
  ref.onDispose(service.dispose);
  return service;
});
