import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../models/anomaly_event.dart';
import '../services/api_service.dart';
import '../providers/user_provider.dart';

class CaregiverScreen extends ConsumerStatefulWidget {
  const CaregiverScreen({super.key});

  @override
  ConsumerState<CaregiverScreen> createState() => _CaregiverScreenState();
}

class _CaregiverScreenState extends ConsumerState<CaregiverScreen> {
  List<AnomalyEvent> _anomalies = [];
  Map<String, dynamic> _routine = {};
  bool _isLoading = true;
  String _isResolvingId = '';

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    setState(() => _isLoading = true);
    try {
      final userId = ref.read(userIdProvider);
      final apiService = ref.read(apiServiceProvider);
      final results = await Future.wait([
        apiService.getActiveAnomalies(userId),
        apiService.getRoutine(userId),
      ]);
      setState(() {
        _anomalies = (results[0] as List<AnomalyEvent>)
          ..sort((a, b) {
            // Sort by severity then time
            const order = {'high': 0, 'medium': 1, 'low': 2};
            final severityCmp = (order[a.severity] ?? 3)
                .compareTo(order[b.severity] ?? 3);
            if (severityCmp != 0) return severityCmp;
            return b.timestamp.compareTo(a.timestamp);
          });
        _routine = results[1] as Map<String, dynamic>;
      });
    } catch (e) {
      _showError('Failed to load alerts: $e');
    } finally {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _resolveAnomaly(AnomalyEvent event) async {
    setState(() => _isResolvingId = event.id ?? '');
    try {
      if (event.id != null) {
        final apiService = ref.read(apiServiceProvider);
        await apiService.resolveAnomaly(event.id!);
      }
      setState(() {
        _anomalies.removeWhere((a) => a.id == event.id);
      });
      _showSuccess('Alert resolved');
    } catch (e) {
      // Still remove from UI even if API fails
      setState(() {
        _anomalies.removeWhere((a) => a.id == event.id);
      });
    } finally {
      setState(() => _isResolvingId = '');
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFFD32F2F),
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
    return Scaffold(
      appBar: AppBar(
        title: const Text('Caregiver Dashboard'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => context.go('/'),
        ),
      ),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: Color(0xFFE65100)),
            )
          : RefreshIndicator(
              onRefresh: _loadData,
              color: const Color(0xFFE65100),
              child: SingleChildScrollView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _buildSummaryCards(),
                    const SizedBox(height: 24),
                    _buildAlertsSection(),
                    const SizedBox(height: 24),
                    _buildRoutineSection(),
                    const SizedBox(height: 24),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _buildSummaryCards() {
    final highCount =
        _anomalies.where((a) => a.severity == 'high').length;
    final medCount =
        _anomalies.where((a) => a.severity == 'medium').length;
    final lowCount =
        _anomalies.where((a) => a.severity == 'low').length;

    // Extract medication info from routine if present
    final medTaken =
        (_routine['medications_taken'] as num?)?.toInt() ?? 0;
    final medTotal =
        (_routine['medications_total'] as num?)?.toInt() ?? 0;
    final adherenceScore =
        (_routine['adherence_score'] as num?)?.toDouble() ?? 0.0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Overview',
          style: GoogleFonts.notoSans(
            fontSize: 24,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: _SummaryCard(
                title: 'Active Alerts',
                value: '${_anomalies.length}',
                icon: Icons.warning_amber,
                color: highCount > 0
                    ? const Color(0xFFD32F2F)
                    : _anomalies.isEmpty
                        ? const Color(0xFF2E7D32)
                        : const Color(0xFFF57C00),
                subtitle: highCount > 0 ? '$highCount high priority' : 'All clear',
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _SummaryCard(
                title: 'Medications',
                value: medTotal > 0 ? '$medTaken/$medTotal' : '--',
                icon: Icons.medication,
                color: medTotal > 0 && medTaken >= medTotal
                    ? const Color(0xFF2E7D32)
                    : const Color(0xFFF57C00),
                subtitle: medTotal > 0
                    ? medTaken >= medTotal
                        ? 'All taken today'
                        : '${medTotal - medTaken} missed'
                    : 'No data',
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: _SummaryCard(
                title: 'Routine Score',
                value: medTotal > 0
                    ? '${(adherenceScore * 100).toInt()}%'
                    : '--',
                icon: Icons.timeline,
                color: adherenceScore >= 0.8
                    ? const Color(0xFF2E7D32)
                    : adherenceScore >= 0.5
                        ? const Color(0xFFF57C00)
                        : const Color(0xFFD32F2F),
                subtitle: 'Today\'s adherence',
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _SummaryCard(
                title: 'Severity',
                value: highCount > 0
                    ? 'HIGH'
                    : medCount > 0
                        ? 'MEDIUM'
                        : 'LOW',
                icon: Icons.shield,
                color: highCount > 0
                    ? const Color(0xFFD32F2F)
                    : medCount > 0
                        ? const Color(0xFFF57C00)
                        : const Color(0xFF2E7D32),
                subtitle:
                    '$highCount high · $medCount med · $lowCount low',
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildAlertsSection() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Active Alerts',
              style: GoogleFonts.notoSans(
                fontSize: 24,
                fontWeight: FontWeight.w700,
              ),
            ),
            if (_anomalies.isNotEmpty)
              TextButton.icon(
                onPressed: () async {
                  final confirm = await showDialog<bool>(
                    context: context,
                    builder: (ctx) => AlertDialog(
                      title: const Text('Resolve All'),
                      content: const Text(
                          'Mark all active alerts as resolved?'),
                      actions: [
                        TextButton(
                          onPressed: () => Navigator.pop(ctx, false),
                          child: const Text('Cancel'),
                        ),
                        ElevatedButton(
                          onPressed: () => Navigator.pop(ctx, true),
                          child: const Text('Resolve All'),
                        ),
                      ],
                    ),
                  );
                  if (confirm == true) {
                    for (final a in List.from(_anomalies)) {
                      await _resolveAnomaly(a);
                    }
                  }
                },
                icon: const Icon(Icons.check_circle_outline, size: 20),
                label: const Text('All'),
              ),
          ],
        ),
        const SizedBox(height: 12),
        if (_anomalies.isEmpty)
          _buildNoAlertsCard()
        else
          ...(_anomalies.map((a) => _buildAlertCard(a)).toList()),
      ],
    );
  }

  Widget _buildNoAlertsCard() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: const Color(0xFFE8F5E9),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFF2E7D32).withOpacity(0.3)),
      ),
      child: Column(
        children: [
          const Icon(
            Icons.check_circle,
            color: Color(0xFF2E7D32),
            size: 48,
          ),
          const SizedBox(height: 12),
          Text(
            'Everything looks good!',
            style: GoogleFonts.notoSans(
              fontSize: 20,
              fontWeight: FontWeight.w700,
              color: const Color(0xFF2E7D32),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'No active alerts at this time',
            style: GoogleFonts.notoSans(
              fontSize: 17,
              color: Colors.green[700],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildAlertCard(AnomalyEvent event) {
    final isResolving = _isResolvingId == event.id;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: event.severityColor.withOpacity(0.4),
          width: 2,
        ),
        boxShadow: [
          BoxShadow(
            color: event.severityColor.withOpacity(0.12),
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: event.severityColor.withOpacity(0.12),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(
                    event.eventIcon,
                    color: event.severityColor,
                    size: 26,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        event.eventTypeLabel,
                        style: GoogleFonts.notoSans(
                          fontSize: 19,
                          fontWeight: FontWeight.w700,
                          color: Colors.black87,
                        ),
                      ),
                      Row(
                        children: [
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: event.severityColor,
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              event.severityLabel,
                              style: GoogleFonts.notoSans(
                                fontSize: 12,
                                fontWeight: FontWeight.w700,
                                color: Colors.white,
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            event.timeAgo,
                            style: GoogleFonts.notoSans(
                              fontSize: 14,
                              color: Colors.grey,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              event.description,
              style: GoogleFonts.notoSans(
                fontSize: 17,
                color: Colors.black87,
                height: 1.4,
              ),
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed:
                    isResolving ? null : () => _resolveAnomaly(event),
                style: ElevatedButton.styleFrom(
                  backgroundColor: event.severityColor,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                ),
                icon: isResolving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(Icons.check),
                label: Text(
                  isResolving ? 'Resolving…' : 'Mark Resolved',
                  style: GoogleFonts.notoSans(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildRoutineSection() {
    if (_routine.isEmpty) return const SizedBox.shrink();

    final activities =
        (_routine['activities'] as List?)?.cast<Map<String, dynamic>>() ?? [];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Today\'s Routine',
          style: GoogleFonts.notoSans(
            fontSize: 24,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 12),
        if (activities.isEmpty)
          Text(
            'No routine data available',
            style: GoogleFonts.notoSans(fontSize: 17, color: Colors.grey),
          )
        else
          ...activities.map((act) => _buildRoutineItem(act)).toList(),
      ],
    );
  }

  Widget _buildRoutineItem(Map<String, dynamic> activity) {
    final name = activity['name']?.toString() ?? 'Activity';
    final completed = activity['completed'] as bool? ?? false;
    final time = activity['time']?.toString() ?? '';

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: completed
            ? const Color(0xFFE8F5E9)
            : const Color(0xFFFFF8F0),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: completed
              ? const Color(0xFF2E7D32).withOpacity(0.3)
              : Colors.grey.withOpacity(0.2),
        ),
      ),
      child: Row(
        children: [
          Icon(
            completed ? Icons.check_circle : Icons.radio_button_unchecked,
            color: completed
                ? const Color(0xFF2E7D32)
                : Colors.grey[400],
            size: 28,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              name,
              style: GoogleFonts.notoSans(
                fontSize: 18,
                color: Colors.black87,
                decoration: completed ? TextDecoration.lineThrough : null,
                decorationColor: Colors.grey,
              ),
            ),
          ),
          if (time.isNotEmpty)
            Text(
              time,
              style: GoogleFonts.notoSans(
                fontSize: 15,
                color: Colors.grey,
              ),
            ),
        ],
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  final String title;
  final String value;
  final IconData icon;
  final Color color;
  final String subtitle;

  const _SummaryCard({
    required this.title,
    required this.value,
    required this.icon,
    required this.color,
    required this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withOpacity(0.3)),
        boxShadow: [
          BoxShadow(
            color: color.withOpacity(0.1),
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
              Icon(icon, color: color, size: 26),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  title,
                  style: GoogleFonts.notoSans(
                    fontSize: 14,
                    color: Colors.grey[600],
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            value,
            style: GoogleFonts.notoSans(
              fontSize: 28,
              fontWeight: FontWeight.w800,
              color: color,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            subtitle,
            style: GoogleFonts.notoSans(
              fontSize: 13,
              color: Colors.grey[500],
            ),
          ),
        ],
      ),
    );
  }
}
