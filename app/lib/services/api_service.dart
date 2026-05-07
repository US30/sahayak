import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:logger/logger.dart';
import '../models/memory_chunk.dart';
import '../models/person.dart';
import '../models/anomaly_event.dart';

final _logger = Logger();

class ApiService {
  static const String _defaultBaseUrl = 'http://10.0.2.2:8000';
  static const String _baseUrlKey = 'base_url';

  late Dio _dio;
  String _baseUrl = _defaultBaseUrl;

  ApiService() {
    _dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 30),
      receiveTimeout: const Duration(seconds: 30),
      sendTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ));
    _dio.interceptors.add(LogInterceptor(
      requestBody: false,
      responseBody: false,
      logPrint: (o) => _logger.d(o.toString()),
    ));
    _loadBaseUrl();
  }

  Future<void> _loadBaseUrl() async {
    final prefs = await SharedPreferences.getInstance();
    _baseUrl = prefs.getString(_baseUrlKey) ?? _defaultBaseUrl;
    _dio.options.baseUrl = _baseUrl;
  }

  Future<void> setBaseUrl(String url) async {
    _baseUrl = url;
    _dio.options.baseUrl = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_baseUrlKey, url);
  }

  String get baseUrl => _baseUrl;

  /// Transcribe audio bytes → returns transcript text
  Future<String> transcribeAudio(Uint8List audioBytes) async {
    await _loadBaseUrl();
    final formData = FormData.fromMap({
      'file': MultipartFile.fromBytes(
        audioBytes,
        filename: 'audio.wav',
        contentType: DioMediaType('audio', 'wav'),
      ),
    });

    final response = await _dio.post(
      '$_baseUrl/transcribe',
      data: formData,
      options: Options(contentType: 'multipart/form-data'),
    );

    final data = response.data;
    if (data is Map) {
      return data['text']?.toString() ??
          data['transcript']?.toString() ??
          '';
    }
    return data?.toString() ?? '';
  }

  /// Query the agent with text, optional base64 image → response text
  Future<String> queryAgent(
    String query,
    String userId, {
    String? imageB64,
  }) async {
    await _loadBaseUrl();
    final body = <String, dynamic>{
      'query': query,
      'user_id': userId,
    };
    if (imageB64 != null) {
      body['image'] = imageB64;
    }

    final response = await _dio.post('$_baseUrl/agent/query', data: body);
    final data = response.data;
    if (data is Map) {
      return data['response']?.toString() ??
          data['text']?.toString() ??
          data['answer']?.toString() ??
          '';
    }
    return data?.toString() ?? '';
  }

  /// Get recent memories for a user
  Future<List<MemoryChunk>> getRecentMemories(
    String userId, {
    int hours = 24,
  }) async {
    await _loadBaseUrl();
    final response = await _dio.get(
      '$_baseUrl/memory/$userId/recent',
      queryParameters: {'hours': hours},
    );
    final data = response.data;
    if (data is List) {
      return data
          .whereType<Map<String, dynamic>>()
          .map((e) => MemoryChunk.fromJson(e))
          .toList();
    }
    return [];
  }

  /// Query memories by search text
  Future<List<MemoryChunk>> queryMemories(
    String userId,
    String query,
  ) async {
    await _loadBaseUrl();
    final response = await _dio.get(
      '$_baseUrl/memory/$userId/query',
      queryParameters: {'q': query},
    );
    final data = response.data;
    if (data is List) {
      return data
          .whereType<Map<String, dynamic>>()
          .map((e) => MemoryChunk.fromJson(e))
          .toList();
    }
    return [];
  }

  /// Store a memory chunk
  Future<void> storeMemory(MemoryChunk chunk) async {
    await _loadBaseUrl();
    await _dio.post('$_baseUrl/memory/store', data: chunk.toJson());
  }

  /// Get registered persons for a user
  Future<List<Person>> getPersons(String userId) async {
    await _loadBaseUrl();
    final response = await _dio.get('$_baseUrl/face/$userId/persons');
    final data = response.data;
    if (data is List) {
      return data
          .whereType<Map<String, dynamic>>()
          .map((e) => Person.fromJson(e))
          .toList();
    }
    return [];
  }

  /// Register a face with a name, relationship, and photo bytes
  Future<Person> registerFace(
    String name,
    String relationship,
    String userId,
    Uint8List imageBytes,
  ) async {
    await _loadBaseUrl();
    final formData = FormData.fromMap({
      'name': name,
      'relationship': relationship,
      'user_id': userId,
      'image': MultipartFile.fromBytes(
        imageBytes,
        filename: 'face.jpg',
        contentType: DioMediaType('image', 'jpeg'),
      ),
    });

    final response = await _dio.post(
      '$_baseUrl/face/register',
      data: formData,
      options: Options(contentType: 'multipart/form-data'),
    );
    return Person.fromJson(response.data as Map<String, dynamic>);
  }

  /// Confirm a registered person
  Future<void> confirmPerson(String personId) async {
    await _loadBaseUrl();
    await _dio.post('$_baseUrl/face/$personId/confirm');
  }

  /// Delete a registered person
  Future<void> deletePerson(String personId) async {
    await _loadBaseUrl();
    await _dio.delete('$_baseUrl/face/$personId');
  }

  /// Get active anomalies for a user
  Future<List<AnomalyEvent>> getActiveAnomalies(String userId) async {
    await _loadBaseUrl();
    final response =
        await _dio.get('$_baseUrl/anomaly/$userId/active');
    final data = response.data;
    if (data is List) {
      return data
          .whereType<Map<String, dynamic>>()
          .map((e) => AnomalyEvent.fromJson(e))
          .toList();
    }
    return [];
  }

  /// Resolve an anomaly event
  Future<void> resolveAnomaly(String anomalyId) async {
    await _loadBaseUrl();
    await _dio.post('$_baseUrl/anomaly/$anomalyId/resolve');
  }

  /// Get routine for a user
  Future<Map<String, dynamic>> getRoutine(String userId) async {
    await _loadBaseUrl();
    final response = await _dio.get('$_baseUrl/routine/$userId');
    return (response.data as Map?)?.cast<String, dynamic>() ?? {};
  }

  /// Health check
  Future<bool> healthCheck() async {
    await _loadBaseUrl();
    try {
      final response = await _dio.get(
        _baseUrl,
        options: Options(receiveTimeout: const Duration(seconds: 5)),
      );
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// Text-to-speech: returns audio bytes
  Future<Uint8List> textToSpeech(String text) async {
    await _loadBaseUrl();
    final response = await _dio.post(
      '$_baseUrl/tts',
      data: {'text': text},
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(response.data as List<int>);
  }

  /// Run evaluation job
  Future<String> runEvaluation() async {
    await _loadBaseUrl();
    final response = await _dio.post('$_baseUrl/eval/run');
    final data = response.data;
    if (data is Map) {
      return data['job_id']?.toString() ??
          data['id']?.toString() ??
          'started';
    }
    return data?.toString() ?? 'started';
  }
}

final apiServiceProvider = Provider<ApiService>((ref) => ApiService());
