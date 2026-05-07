import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

const String _userIdKey = 'user_id';
const String _languageKey = 'language';
const String _voiceSpeedKey = 'voice_speed';
const String _defaultUserId = 'user_001';

/// Provider that holds and persists the user ID
class UserIdNotifier extends StateNotifier<String> {
  UserIdNotifier() : super(_defaultUserId) {
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    state = prefs.getString(_userIdKey) ?? _defaultUserId;
  }

  Future<void> setUserId(String id) async {
    state = id;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_userIdKey, id);
  }
}

final userIdProvider = StateNotifierProvider<UserIdNotifier, String>(
  (ref) => UserIdNotifier(),
);

/// Provider for language preference
class LanguageNotifier extends StateNotifier<String> {
  LanguageNotifier() : super('auto') {
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    state = prefs.getString(_languageKey) ?? 'auto';
  }

  Future<void> setLanguage(String lang) async {
    state = lang;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_languageKey, lang);
  }
}

final languageProvider = StateNotifierProvider<LanguageNotifier, String>(
  (ref) => LanguageNotifier(),
);

/// Provider for voice speed (0.5 – 2.0)
class VoiceSpeedNotifier extends StateNotifier<double> {
  VoiceSpeedNotifier() : super(1.0) {
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    state = prefs.getDouble(_voiceSpeedKey) ?? 1.0;
  }

  Future<void> setSpeed(double speed) async {
    state = speed;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_voiceSpeedKey, speed);
  }
}

final voiceSpeedProvider = StateNotifierProvider<VoiceSpeedNotifier, double>(
  (ref) => VoiceSpeedNotifier(),
);
