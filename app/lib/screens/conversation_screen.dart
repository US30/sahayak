import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../models/conversation_message.dart';
import '../services/api_service.dart';
import '../services/audio_service.dart';
import '../providers/user_provider.dart';
import '../providers/conversation_provider.dart';

class ConversationScreen extends ConsumerStatefulWidget {
  const ConversationScreen({super.key});

  @override
  ConsumerState<ConversationScreen> createState() =>
      _ConversationScreenState();
}

class _ConversationScreenState extends ConsumerState<ConversationScreen> {
  bool _isRefreshing = false;
  bool _isRecording = false;
  bool _isProcessing = false;
  final ScrollController _scrollController = ScrollController();

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() => _isRefreshing = true);
    try {
      final userId = ref.read(userIdProvider);
      final apiService = ref.read(apiServiceProvider);
      final memories = await apiService.getRecentMemories(userId, hours: 48);
      final notifier = ref.read(conversationProvider.notifier);
      // Load memory conversations into the conversation list
      for (final mem in memories) {
        if (mem.memoryType == 'conversation') {
          notifier.addSahayakMessage(mem.text);
        }
      }
    } catch (e) {
      _showError('Failed to load history: $e');
    } finally {
      setState(() => _isRefreshing = false);
    }
  }

  Future<void> _handleVoiceInput() async {
    final audioService = ref.read(audioServiceProvider);
    final apiService = ref.read(apiServiceProvider);
    final userId = ref.read(userIdProvider);
    final conversationNotifier = ref.read(conversationProvider.notifier);

    if (_isRecording) {
      setState(() {
        _isRecording = false;
        _isProcessing = true;
      });
      try {
        final bytes = await audioService.stopRecording();
        final transcribed = await apiService.transcribeAudio(bytes);
        if (transcribed.isNotEmpty) {
          conversationNotifier.addUserMessage(transcribed);
          final response = await apiService.queryAgent(transcribed, userId);
          conversationNotifier.addSahayakMessage(response);
          try {
            await audioService.playText(response, apiService);
          } catch (_) {}
          _scrollToBottom();
        }
      } catch (e) {
        _showError('Error: $e');
      } finally {
        setState(() => _isProcessing = false);
      }
    } else {
      try {
        await audioService.startRecording();
        setState(() => _isRecording = true);
      } catch (e) {
        _showError('Could not start recording: $e');
      }
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFFD32F2F),
        duration: const Duration(seconds: 4),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final messages = ref.watch(conversationProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Conversations'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => context.go('/'),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.delete_outline),
            tooltip: 'Clear history',
            onPressed: () => _confirmClear(context),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: RefreshIndicator(
              onRefresh: _refresh,
              color: const Color(0xFFE65100),
              child: messages.isEmpty
                  ? _buildEmptyState()
                  : ListView.builder(
                      controller: _scrollController,
                      padding: const EdgeInsets.all(16),
                      itemCount: messages.length,
                      itemBuilder: (context, index) {
                        final msg = messages[index];
                        final showDateHeader = index == 0 ||
                            _isDifferentDay(
                              messages[index - 1].timestamp,
                              msg.timestamp,
                            );
                        return Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            if (showDateHeader)
                              _buildDateHeader(msg.timestamp),
                            _buildMessageBubble(msg),
                          ],
                        );
                      },
                    ),
            ),
          ),
          if (_isProcessing)
            Container(
              padding: const EdgeInsets.all(12),
              color: const Color(0xFFFFF3E0),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2.5,
                      color: Color(0xFFE65100),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    'Sahayak is thinking…',
                    style: GoogleFonts.notoSans(
                      fontSize: 16,
                      color: const Color(0xFFE65100),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _isProcessing ? null : _handleVoiceInput,
        backgroundColor: _isRecording
            ? const Color(0xFFD32F2F)
            : const Color(0xFFE65100),
        icon: Icon(_isRecording ? Icons.stop : Icons.mic),
        label: Text(
          _isRecording ? 'Stop' : 'Speak',
          style: GoogleFonts.notoSans(
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            Icons.chat_bubble_outline,
            size: 80,
            color: Colors.grey[300],
          ),
          const SizedBox(height: 16),
          Text(
            'No conversations yet',
            style: GoogleFonts.notoSans(
              fontSize: 22,
              color: Colors.grey,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Pull down to load history\nor tap the mic to start',
            style: GoogleFonts.notoSans(
              fontSize: 17,
              color: Colors.grey[400],
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  bool _isDifferentDay(DateTime a, DateTime b) {
    return a.year != b.year || a.month != b.month || a.day != b.day;
  }

  Widget _buildDateHeader(DateTime date) {
    final now = DateTime.now();
    final isToday =
        date.year == now.year && date.month == now.month && date.day == now.day;
    final yesterday = now.subtract(const Duration(days: 1));
    final isYesterday = date.year == yesterday.year &&
        date.month == yesterday.month &&
        date.day == yesterday.day;

    final label = isToday
        ? 'Today'
        : isYesterday
            ? 'Yesterday'
            : _formatDate(date);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Row(
        children: [
          Expanded(child: Divider(color: Colors.grey[300])),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              label,
              style: GoogleFonts.notoSans(
                fontSize: 15,
                color: Colors.grey,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Expanded(child: Divider(color: Colors.grey[300])),
        ],
      ),
    );
  }

  String _formatDate(DateTime date) {
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[date.month - 1]} ${date.day}';
  }

  Widget _buildMessageBubble(ConversationMessage msg) {
    final isUser = msg.isUser;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        mainAxisAlignment:
            isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (!isUser) ...[
            Container(
              width: 40,
              height: 40,
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
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Column(
              crossAxisAlignment: isUser
                  ? CrossAxisAlignment.end
                  : CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 18,
                    vertical: 14,
                  ),
                  decoration: BoxDecoration(
                    color: isUser
                        ? Colors.grey[200]
                        : const Color(0xFFFFE0B2),
                    borderRadius: BorderRadius.only(
                      topLeft: const Radius.circular(18),
                      topRight: const Radius.circular(18),
                      bottomLeft: Radius.circular(isUser ? 18 : 4),
                      bottomRight: Radius.circular(isUser ? 4 : 18),
                    ),
                    border: isUser
                        ? null
                        : Border.all(
                            color: const Color(0xFFFFB300),
                            width: 1,
                          ),
                  ),
                  constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.72,
                  ),
                  child: Text(
                    msg.text,
                    style: GoogleFonts.notoSans(
                      fontSize: 19,
                      color: Colors.black87,
                      height: 1.5,
                    ),
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  msg.formattedTime,
                  style: GoogleFonts.notoSans(
                    fontSize: 13,
                    color: Colors.grey,
                  ),
                ),
              ],
            ),
          ),
          if (isUser) ...[
            const SizedBox(width: 8),
            CircleAvatar(
              radius: 20,
              backgroundColor: Colors.grey[300],
              child: const Icon(Icons.person, color: Colors.grey, size: 22),
            ),
          ],
        ],
      ),
    );
  }

  void _confirmClear(BuildContext context) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear History'),
        content: const Text(
          'This will remove all messages from view. Your memories stored in the app will not be deleted.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: () {
              ref.read(conversationProvider.notifier).clear();
              Navigator.pop(ctx);
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFD32F2F),
            ),
            child: const Text('Clear'),
          ),
        ],
      ),
    );
  }
}
