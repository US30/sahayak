import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../services/api_service.dart';
import '../services/audio_service.dart';
import '../providers/user_provider.dart';
import '../providers/conversation_provider.dart';

enum _MicState { idle, recording, processing, speaking }

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen>
    with TickerProviderStateMixin {
  _MicState _micState = _MicState.idle;
  String _lastResponse = '';
  String _lastUserText = '';

  late AnimationController _pulseController;
  late Animation<double> _pulseAnimation;
  late AnimationController _glowController;
  late Animation<double> _glowAnimation;

  Timer? _clockTimer;
  String _timeString = '';

  @override
  void initState() {
    super.initState();
    _updateTime();
    _clockTimer = Timer.periodic(const Duration(seconds: 30), (_) => _updateTime());

    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);

    _pulseAnimation = Tween<double>(begin: 0.92, end: 1.08).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );

    _glowController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);

    _glowAnimation = Tween<double>(begin: 6.0, end: 24.0).animate(
      CurvedAnimation(parent: _glowController, curve: Curves.easeInOut),
    );
  }

  void _updateTime() {
    final now = DateTime.now();
    final hour = now.hour;
    final minute = now.minute.toString().padLeft(2, '0');
    final period = hour >= 12 ? 'PM' : 'AM';
    final displayHour = hour > 12 ? hour - 12 : (hour == 0 ? 12 : hour);
    setState(() {
      _timeString = '$displayHour:$minute $period';
    });
  }

  @override
  void dispose() {
    _pulseController.dispose();
    _glowController.dispose();
    _clockTimer?.cancel();
    super.dispose();
  }

  Future<void> _handleMicTap() async {
    final audioService = ref.read(audioServiceProvider);
    final apiService = ref.read(apiServiceProvider);
    final userId = ref.read(userIdProvider);
    final conversationNotifier = ref.read(conversationProvider.notifier);

    switch (_micState) {
      case _MicState.idle:
        try {
          await audioService.startRecording();
          setState(() => _micState = _MicState.recording);
        } catch (e) {
          _showError('Could not start recording: $e');
        }
        break;

      case _MicState.recording:
        setState(() => _micState = _MicState.processing);
        try {
          final bytes = await audioService.stopRecording();

          // Transcribe
          final transcribed = await apiService.transcribeAudio(bytes);
          if (transcribed.isEmpty) {
            setState(() {
              _micState = _MicState.idle;
              _lastResponse = 'Could not understand. Please try again.';
            });
            return;
          }

          setState(() => _lastUserText = transcribed);
          conversationNotifier.addUserMessage(transcribed);

          // Query agent
          final response = await apiService.queryAgent(transcribed, userId);
          conversationNotifier.addSahayakMessage(response);

          setState(() {
            _lastResponse = response;
            _micState = _MicState.speaking;
          });

          // Play TTS
          try {
            await audioService.playText(response, apiService);
          } catch (_) {
            // TTS failure is non-fatal — show text anyway
          }

          setState(() => _micState = _MicState.idle);
        } catch (e) {
          setState(() => _micState = _MicState.idle);
          _showError('Error: $e');
        }
        break;

      case _MicState.processing:
      case _MicState.speaking:
        // Do nothing while busy
        break;
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFFD32F2F),
        duration: const Duration(seconds: 4),
        action: SnackBarAction(
          label: 'OK',
          textColor: Colors.white,
          onPressed: () =>
              ScaffoldMessenger.of(context).hideCurrentSnackBar(),
        ),
      ),
    );
  }

  Color get _micColor {
    switch (_micState) {
      case _MicState.idle:
        return const Color(0xFFE65100);
      case _MicState.recording:
        return const Color(0xFFD32F2F);
      case _MicState.processing:
        return const Color(0xFF1565C0);
      case _MicState.speaking:
        return const Color(0xFF2E7D32);
    }
  }

  IconData get _micIcon {
    switch (_micState) {
      case _MicState.idle:
        return Icons.mic;
      case _MicState.recording:
        return Icons.stop;
      case _MicState.processing:
        return Icons.hourglass_bottom;
      case _MicState.speaking:
        return Icons.volume_up;
    }
  }

  String get _micLabel {
    switch (_micState) {
      case _MicState.idle:
        return 'Tap to speak';
      case _MicState.recording:
        return 'Tap to stop';
      case _MicState.processing:
        return 'Thinking…';
      case _MicState.speaking:
        return 'Speaking…';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(context),
            Expanded(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  _buildMicButton(),
                  const SizedBox(height: 32),
                  _buildResponseArea(),
                ],
              ),
            ),
            _buildBottomNav(context),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          colors: [Color(0xFFE65100), Color(0xFFFF8F00)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Sahayak',
                style: GoogleFonts.notoSans(
                  fontSize: 32,
                  fontWeight: FontWeight.w800,
                  color: Colors.white,
                ),
              ),
              Text(
                'Your companion',
                style: GoogleFonts.notoSans(
                  fontSize: 16,
                  color: Colors.white70,
                ),
              ),
            ],
          ),
          Row(
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    _timeString,
                    style: GoogleFonts.notoSans(
                      fontSize: 28,
                      fontWeight: FontWeight.w700,
                      color: Colors.white,
                    ),
                  ),
                  Text(
                    _todayDateString(),
                    style: GoogleFonts.notoSans(
                      fontSize: 15,
                      color: Colors.white70,
                    ),
                  ),
                ],
              ),
              const SizedBox(width: 12),
              IconButton(
                onPressed: () => context.go('/settings'),
                icon: const Icon(Icons.settings, color: Colors.white, size: 28),
                tooltip: 'Settings',
              ),
            ],
          ),
        ],
      ),
    );
  }

  String _todayDateString() {
    final now = DateTime.now();
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return '${days[now.weekday - 1]}, ${months[now.month - 1]} ${now.day}';
  }

  Widget _buildMicButton() {
    final isActive =
        _micState == _MicState.recording || _micState == _MicState.speaking;
    final isProcessing = _micState == _MicState.processing;

    return Column(
      children: [
        AnimatedBuilder(
          animation: Listenable.merge([_pulseAnimation, _glowAnimation]),
          builder: (context, child) {
            final scale = isActive ? _pulseAnimation.value : 1.0;
            final glowRadius = isActive ? _glowAnimation.value : 6.0;

            return Transform.scale(
              scale: scale,
              child: GestureDetector(
                onTap: isProcessing ? null : _handleMicTap,
                child: Container(
                  width: 160,
                  height: 160,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _micColor,
                    boxShadow: [
                      BoxShadow(
                        color: _micColor.withOpacity(0.5),
                        blurRadius: glowRadius,
                        spreadRadius: glowRadius / 3,
                      ),
                      BoxShadow(
                        color: _micColor.withOpacity(0.2),
                        blurRadius: glowRadius * 2,
                        spreadRadius: glowRadius / 2,
                      ),
                    ],
                  ),
                  child: isProcessing
                      ? const Center(
                          child: SizedBox(
                            width: 64,
                            height: 64,
                            child: CircularProgressIndicator(
                              color: Colors.white,
                              strokeWidth: 5,
                            ),
                          ),
                        )
                      : Icon(
                          _micIcon,
                          color: Colors.white,
                          size: 80,
                        ),
                ),
              ),
            );
          },
        ),
        const SizedBox(height: 20),
        Text(
          _micLabel,
          style: GoogleFonts.notoSans(
            fontSize: 22,
            fontWeight: FontWeight.w600,
            color: _micState == _MicState.idle
                ? Colors.black54
                : _micColor,
          ),
        ),
      ],
    );
  }

  Widget _buildResponseArea() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        children: [
          if (_lastUserText.isNotEmpty) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.grey[200],
                borderRadius: BorderRadius.circular(16),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.person, color: Colors.grey, size: 24),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      _lastUserText,
                      style: GoogleFonts.notoSans(
                        fontSize: 18,
                        color: Colors.black87,
                      ),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
          ],
          if (_lastResponse.isNotEmpty) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(18),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0xFFFFF3E0), Color(0xFFFFE0B2)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: const Color(0xFFFFB300),
                  width: 1.5,
                ),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    padding: const EdgeInsets.all(6),
                    decoration: const BoxDecoration(
                      color: Color(0xFFE65100),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.smart_toy,
                      color: Colors.white,
                      size: 22,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      _lastResponse,
                      style: GoogleFonts.notoSans(
                        fontSize: 20,
                        color: Colors.black87,
                        height: 1.5,
                      ),
                      maxLines: 5,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ],
          if (_lastResponse.isEmpty && _lastUserText.isEmpty)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                'Tap the microphone and speak.\nSahayak will listen and respond.',
                style: GoogleFonts.notoSans(
                  fontSize: 18,
                  color: Colors.black45,
                  height: 1.6,
                ),
                textAlign: TextAlign.center,
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildBottomNav(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.1),
            blurRadius: 8,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _NavItem(
                icon: Icons.home,
                label: 'Home',
                selected: true,
                onTap: () {},
              ),
              _NavItem(
                icon: Icons.history,
                label: 'Memories',
                selected: false,
                onTap: () => context.go('/memory'),
              ),
              _NavItem(
                icon: Icons.people,
                label: 'Faces',
                selected: false,
                onTap: () => context.go('/faces'),
              ),
              _NavItem(
                icon: Icons.notifications,
                label: 'Alerts',
                selected: false,
                onTap: () => context.go('/caregiver'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _NavItem({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final color =
        selected ? const Color(0xFFE65100) : Colors.grey;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: color, size: 30),
            const SizedBox(height: 4),
            Text(
              label,
              style: GoogleFonts.notoSans(
                fontSize: 14,
                fontWeight:
                    selected ? FontWeight.w700 : FontWeight.normal,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
