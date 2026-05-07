import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/api_service.dart';
import '../providers/user_provider.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  late TextEditingController _userIdController;
  late TextEditingController _baseUrlController;

  bool _isTestingConnection = false;
  bool _isRunningEval = false;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    final currentUserId = ref.read(userIdProvider);
    _userIdController = TextEditingController(text: currentUserId);
    _baseUrlController = TextEditingController();
    _loadBaseUrl();
  }

  Future<void> _loadBaseUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final url =
        prefs.getString('base_url') ?? 'http://10.0.2.2:8000';
    setState(() => _baseUrlController.text = url);
  }

  @override
  void dispose() {
    _userIdController.dispose();
    _baseUrlController.dispose();
    super.dispose();
  }

  Future<void> _saveSettings() async {
    setState(() => _isSaving = true);
    try {
      final newUserId = _userIdController.text.trim();
      if (newUserId.isNotEmpty) {
        await ref.read(userIdProvider.notifier).setUserId(newUserId);
      }

      final newUrl = _baseUrlController.text.trim();
      if (newUrl.isNotEmpty) {
        final apiService = ref.read(apiServiceProvider);
        await apiService.setBaseUrl(newUrl);
      }

      _showSuccess('Settings saved!');
    } catch (e) {
      _showError('Failed to save settings: $e');
    } finally {
      setState(() => _isSaving = false);
    }
  }

  Future<void> _testConnection() async {
    setState(() => _isTestingConnection = true);
    try {
      // Update URL first
      final newUrl = _baseUrlController.text.trim();
      if (newUrl.isNotEmpty) {
        final apiService = ref.read(apiServiceProvider);
        await apiService.setBaseUrl(newUrl);
      }

      final apiService = ref.read(apiServiceProvider);
      final ok = await apiService.healthCheck();

      if (!mounted) return;
      if (ok) {
        _showSuccess('Connection successful! Backend is reachable.');
      } else {
        _showError('Backend returned an unexpected response. Check the URL.');
      }
    } catch (e) {
      _showError('Connection failed: $e');
    } finally {
      setState(() => _isTestingConnection = false);
    }
  }

  Future<void> _runEvaluation() async {
    setState(() => _isRunningEval = true);
    try {
      final apiService = ref.read(apiServiceProvider);
      final jobId = await apiService.runEvaluation();
      _showSuccess('Evaluation started! Job ID: $jobId');
    } catch (e) {
      _showError('Failed to start evaluation: $e');
    } finally {
      setState(() => _isRunningEval = false);
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFFD32F2F),
        duration: const Duration(seconds: 5),
      ),
    );
  }

  void _showSuccess(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFF2E7D32),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final language = ref.watch(languageProvider);
    final voiceSpeed = ref.watch(voiceSpeedProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => context.go('/'),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          _buildSection(
            title: 'User Profile',
            icon: Icons.person,
            children: [
              _buildLabel('User ID'),
              const SizedBox(height: 8),
              TextField(
                controller: _userIdController,
                style: GoogleFonts.notoSans(fontSize: 18),
                decoration: const InputDecoration(
                  hintText: 'e.g. user_001',
                  prefixIcon: Icon(Icons.badge),
                ),
              ),
              const SizedBox(height: 6),
              Text(
                'This identifies you in the system. Ask your caregiver for your ID.',
                style: GoogleFonts.notoSans(
                    fontSize: 14, color: Colors.grey[600]),
              ),
            ],
          ),
          const SizedBox(height: 20),
          _buildSection(
            title: 'Backend Connection',
            icon: Icons.cloud,
            children: [
              _buildLabel('API Base URL'),
              const SizedBox(height: 8),
              TextField(
                controller: _baseUrlController,
                style: GoogleFonts.notoSans(fontSize: 18),
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  hintText: 'http://10.0.2.2:8000',
                  prefixIcon: Icon(Icons.link),
                ),
              ),
              const SizedBox(height: 6),
              Text(
                'Android emulator uses 10.0.2.2 to reach your computer\'s localhost.',
                style: GoogleFonts.notoSans(
                    fontSize: 14, color: Colors.grey[600]),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed:
                      _isTestingConnection ? null : _testConnection,
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    side: const BorderSide(
                        color: Color(0xFFE65100), width: 2),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  icon: _isTestingConnection
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2.5,
                            color: Color(0xFFE65100),
                          ),
                        )
                      : const Icon(
                          Icons.wifi_find,
                          color: Color(0xFFE65100),
                        ),
                  label: Text(
                    _isTestingConnection
                        ? 'Testing…'
                        : 'Test Connection',
                    style: GoogleFonts.notoSans(
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: const Color(0xFFE65100),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          _buildSection(
            title: 'Voice & Language',
            icon: Icons.record_voice_over,
            children: [
              _buildLabel('Language Preference'),
              const SizedBox(height: 8),
              _buildLanguageToggle(language),
              const SizedBox(height: 20),
              _buildLabel(
                  'Voice Speed: ${voiceSpeed.toStringAsFixed(1)}x'),
              const SizedBox(height: 4),
              Slider(
                value: voiceSpeed,
                min: 0.5,
                max: 2.0,
                divisions: 6,
                activeColor: const Color(0xFFE65100),
                inactiveColor: const Color(0xFFFFE0B2),
                label: '${voiceSpeed.toStringAsFixed(1)}x',
                onChanged: (v) {
                  ref.read(voiceSpeedProvider.notifier).setSpeed(v);
                },
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('Slow (0.5x)',
                      style: GoogleFonts.notoSans(
                          fontSize: 13, color: Colors.grey)),
                  Text('Fast (2.0x)',
                      style: GoogleFonts.notoSans(
                          fontSize: 13, color: Colors.grey)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 20),
          _buildSection(
            title: 'Developer Tools',
            icon: Icons.developer_mode,
            children: [
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: _isRunningEval ? null : _runEvaluation,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF37474F),
                    padding: const EdgeInsets.symmetric(vertical: 14),
                  ),
                  icon: _isRunningEval
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2.5,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.science),
                  label: Text(
                    _isRunningEval ? 'Starting…' : 'Run Evaluation',
                    style: GoogleFonts.notoSans(
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Runs a backend evaluation job and returns a job ID.',
                style: GoogleFonts.notoSans(
                    fontSize: 14, color: Colors.grey[600]),
              ),
            ],
          ),
          const SizedBox(height: 24),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              onPressed: _isSaving ? null : _saveSettings,
              style: ElevatedButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 18),
              ),
              icon: _isSaving
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(
                        strokeWidth: 2.5,
                        color: Colors.white,
                      ),
                    )
                  : const Icon(Icons.save, size: 24),
              label: Text(
                _isSaving ? 'Saving…' : 'Save Settings',
                style: GoogleFonts.notoSans(
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ),
          const SizedBox(height: 40),
          Center(
            child: Text(
              'Sahayak v1.0.0',
              style: GoogleFonts.notoSans(
                fontSize: 14,
                color: Colors.grey[400],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSection({
    required String title,
    required IconData icon,
    required List<Widget> children,
  }) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(18),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.05),
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: const Color(0xFFE65100), size: 26),
              const SizedBox(width: 10),
              Text(
                title,
                style: GoogleFonts.notoSans(
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                  color: const Color(0xFFE65100),
                ),
              ),
            ],
          ),
          const Divider(height: 20),
          ...children,
        ],
      ),
    );
  }

  Widget _buildLabel(String text) {
    return Text(
      text,
      style: GoogleFonts.notoSans(
        fontSize: 17,
        fontWeight: FontWeight.w600,
        color: Colors.black87,
      ),
    );
  }

  Widget _buildLanguageToggle(String current) {
    const options = [
      {'value': 'hindi', 'label': 'Hindi', 'icon': '🇮🇳'},
      {'value': 'english', 'label': 'English', 'icon': '🇬🇧'},
      {'value': 'auto', 'label': 'Auto', 'icon': '🌐'},
    ];

    return Row(
      children: options.map((opt) {
        final isSelected = current == opt['value'];
        return Expanded(
          child: GestureDetector(
            onTap: () {
              ref
                  .read(languageProvider.notifier)
                  .setLanguage(opt['value']!);
            },
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              margin: const EdgeInsets.symmetric(horizontal: 4),
              padding: const EdgeInsets.symmetric(
                  vertical: 12, horizontal: 4),
              decoration: BoxDecoration(
                color: isSelected
                    ? const Color(0xFFE65100)
                    : Colors.grey[100],
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: isSelected
                      ? const Color(0xFFE65100)
                      : Colors.grey[300]!,
                ),
              ),
              child: Column(
                children: [
                  Text(
                    opt['icon']!,
                    style: const TextStyle(fontSize: 22),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    opt['label']!,
                    style: GoogleFonts.notoSans(
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                      color: isSelected ? Colors.white : Colors.black54,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      }).toList(),
    );
  }
}
