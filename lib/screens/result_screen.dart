import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';
import '../models/pairing_result.dart';
import '../services/api_service.dart';
import '../services/storage_service.dart';
import 'paywall_screen.dart';

class ResultScreen extends StatefulWidget {
  // Для истории / избранного — уже готовый ответ
  final PairingResponse? response;

  // Для нового поиска — стриминг с нуля
  final String? dish;
  final String? mode;
  final String? budget;

  const ResultScreen({
    super.key,
    this.response,
    this.dish,
    this.mode,
    this.budget,
  }) : assert(
          response != null || (dish != null && mode != null && budget != null),
          'Either response or dish/mode/budget must be provided',
        );

  @override
  State<ResultScreen> createState() => _ResultScreenState();
}

class _ResultScreenState extends State<ResultScreen>
    with SingleTickerProviderStateMixin {
  static const _gold = Color(0xFFC9A84C);
  static const _goldText = Color(0xFFD4B563);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  PairingResponse? _response;
  bool _isLoading = true;
  bool _isSaved = false;
  bool _isSavedChecked = false;
  // Для sequential fade кнопки "Сохранить → Сохранено": сначала _fadingOut=true
  // делает opacity 0, потом меняем _isSaved=true и opacity обратно в 1.
  // AnimatedSwitcher cross-fade давал эффект перепечатки концовки.
  bool _fadingOut = false;
  bool _isSharing = false;
  String? _error;

  // GlobalKey для RepaintBoundary вокруг первой карточки + footer "Дуэт".
  // Используется в _shareFirstCard для рендера в PNG через RenderRepaintBoundary.toImage.
  final GlobalKey _shareCardKey = GlobalKey();

  late final AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);

    if (widget.response != null) {
      _response = widget.response;
      _isLoading = false;
      _checkIfSaved();
    } else {
      _startStream();
    }
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  Future<void> _checkIfSaved() async {
    if (_response == null) return;
    final favorites = await ApiService.getFavorites();
    final saved = favorites.any((f) => f.dish == _response!.dish && f.budget == _response!.budget);
    if (mounted) setState(() { _isSaved = saved; _isSavedChecked = true; });
  }

  Future<void> _startStream() async {
    final buffer = StringBuffer();
    try {
      await for (final chunk in ApiService.pairStream(
        dish: widget.dish!,
        mode: widget.mode!,
        budget: widget.budget!,
      )) {
        buffer.write(chunk);
      }

      var raw = buffer.toString().trim();

      // Убираем markdown-обёртку ДО парсинга — Claude иногда оборачивает JSON в ```json
      if (raw.startsWith('```')) {
        raw = raw.split('\n').skip(1).join('\n');
        raw = raw.substring(0, raw.lastIndexOf('```')).trim();
      }

      // Парсим JSON. Если Claude вернул свободный текст (невалидный запрос,
      // где модель проигнорировала инструкцию про error-JSON) — ловим
      // FormatException и показываем понятное сообщение вместо техническое.
      final dynamic data;
      try {
        data = jsonDecode(raw);
      } on FormatException {
        throw Exception('Не удалось распознать блюдо. Попробуйте уточнить название или выбрать другое.');
      }

      // Claude вернул поле error — невалидный запрос (корм для животных,
      // бессмыслица, оскорбления). Показываем текст error как user message.
      if (data is Map && data['error'] != null) {
        throw Exception(data['error'].toString());
      }

      // Защитный слой на brand: срезаем скобки со страной "(Германия)" и
      // любые trailing альтернативы ("X или Y", "X / Y"). Бэкенд делает то же
      // самое при сохранении, но кеш может содержать старые записи — чистим
      // на всякий случай и на фронте. Цель — чтобы ссылка в Kaspi/Magnum была
      // чистая и магазин находил товар.
      final resultsList = (data['results'] as List);
      for (final r in resultsList) {
        if (r is Map && r['brand'] is String) {
          var brand = r['brand'] as String;
          brand = brand.replaceAll(RegExp(r'\s*\([^)]*\)'), '');
          brand = brand.split(RegExp(r'\s+(?:или|/|,|\sлибо\s|\s—\s)')).first;
          r['brand'] = brand.trim();
        }
      }
      final prefs = await SharedPreferences.getInstance();
      final region = prefs.getString('region') ?? 'СНГ';

      final response = PairingResponse(
        dish: widget.dish!,
        mode: widget.mode!,
        budget: widget.budget!,
        region: region,
        results: (data['results'] as List)
            .take(3)
            .map((r) => PairingResult.fromJson(r))
            .toList(),
        createdAt: DateTime.now(),
      );

      await StorageService.saveToHistory(response);

      if (mounted) {
        setState(() {
          _response = response;
          _isLoading = false;
        });
      }
    } on PairingLimitException catch (_) {
      // Лимит подборок исчерпан — редиректим на Paywall с персонализацией.
      // pushReplacement чтобы пользователь не мог вернуться кнопкой "Назад"
      // на пустой экран загрузки.
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(
            builder: (_) => PaywallScreen(
              dish: widget.dish!,
              mode: widget.mode!,
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString().replaceAll('Exception: ', '');
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: _buildAppBar(context),
      body: Stack(
        children: [
          ListView(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
            children: [
              _buildDishHeader(),
              const SizedBox(height: 24),
              _buildResultsLabel(),
              const SizedBox(height: 12),
              if (_isLoading) ...[
                _buildSkeletonCard(),
                _buildSkeletonCard(),
                _buildSkeletonCard(),
              ] else if (_error != null)
                _buildError()
              else ...[
                ...(_response!.results.asMap().entries.map(
                      (e) => _buildResultCard(e.key + 1, e.value),
                    )),
                const SizedBox(height: 24),
                _buildSaveButton(context),
              ],
              const SizedBox(height: 16),
              SizedBox(height: MediaQuery.of(context).padding.bottom),
            ],
          ),
          // Offscreen-виджет для share: первая карточка + футер "Дуэт".
          // Position за пределами экрана (-2000), но в дереве виджетов —
          // RepaintBoundary рендерится и доступен через GlobalKey.
          if (_response != null && _response!.results.isNotEmpty)
            Positioned(
              left: -2000,
              top: 0,
              child: _buildShareableCard(_response!.results.first),
            ),
        ],
      ),
    );
  }

  /// Виджет для share — первая карточка + футер "Дуэт".
  /// Рендерится offscreen, в скриншот идёт ровно эта композиция.
  Widget _buildShareableCard(PairingResult result) {
    return RepaintBoundary(
      key: _shareCardKey,
      child: Container(
        width: 380,
        padding: const EdgeInsets.all(20),
        decoration: const BoxDecoration(color: _bg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Заголовок над карточкой: блюдо/напиток
            Row(
              children: [
                Text(
                  (_response?.mode ?? 'food_to_alcohol') == 'food_to_alcohol' ? '🍽️' : '🥂',
                  style: const TextStyle(fontSize: 22),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    _response?.dish ?? '',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            // Сама карточка результата (упрощённая для шаринга)
            Container(
              padding: const EdgeInsets.all(18),
              decoration: BoxDecoration(
                color: _card,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: _gold.withOpacity(0.4), width: 1.5),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Text(result.resolvedEmoji, style: const TextStyle(fontSize: 18)),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          result.alcoholType,
                          style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 12),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    result.name,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 20,
                      fontWeight: FontWeight.w700,
                    ),
                    maxLines: 2,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    result.brand,
                    style: const TextStyle(color: _goldText, fontSize: 14),
                    maxLines: 2,
                  ),
                  const SizedBox(height: 14),
                  Text(
                    result.reason,
                    style: TextStyle(
                      color: Colors.white.withOpacity(0.75),
                      fontSize: 13,
                      height: 1.5,
                    ),
                    maxLines: 6,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            // Футер "Дуэт" — брендинг без URL по решению маркетинга
            Center(
              child: Column(
                children: [
                  Text(
                    'Дуэт',
                    style: TextStyle(
                      color: _gold,
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 2,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    'AI-эксперт по напиткам к еде',
                    style: TextStyle(
                      color: Colors.white.withOpacity(0.4),
                      fontSize: 11,
                      letterSpacing: 0.3,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  PreferredSizeWidget _buildAppBar(BuildContext context) {
    // Кнопка Поделиться активна только когда подборка реально готова (есть results).
    final canShare = _response != null && _response!.results.isNotEmpty && !_isSharing;
    return AppBar(
      backgroundColor: _bg,
      surfaceTintColor: Colors.transparent,
      leading: GestureDetector(
        onTap: () => Navigator.pop(context),
        child: const Icon(Icons.arrow_back_ios_rounded, color: Colors.white54, size: 20),
      ),
      title: const Text(
        'Дуэт',
        style: TextStyle(color: _gold, fontSize: 18, fontWeight: FontWeight.w700, letterSpacing: 1),
      ),
      centerTitle: true,
      actions: [
        IconButton(
          icon: _isSharing
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2, color: _gold),
                )
              : Icon(
                  Icons.ios_share_rounded,
                  color: canShare ? _gold : Colors.white.withOpacity(0.2),
                  size: 22,
                ),
          tooltip: 'Поделиться',
          onPressed: canShare ? _shareFirstCard : null,
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  /// Делает скриншот первой карточки результата с футером "Дуэт" и расшаривает
  /// его через системный share sheet. Используется RepaintBoundary с GlobalKey
  /// + RenderRepaintBoundary.toImage → PNG → файл во временной папке → Share.shareXFiles.
  /// Целевой канал: Instagram Stories (вертикальная компактная карточка с брендингом).
  Future<void> _shareFirstCard() async {
    if (_response == null || _response!.results.isEmpty) return;
    setState(() => _isSharing = true);
    HapticFeedback.lightImpact();
    try {
      // Ждём один кадр чтобы offscreen RepaintBoundary гарантированно отрендерился
      await Future.delayed(const Duration(milliseconds: 50));

      final boundary = _shareCardKey.currentContext?.findRenderObject() as RenderRepaintBoundary?;
      if (boundary == null) throw Exception('share boundary not found');

      // pixelRatio 3.0 = retina-качество, хорошо смотрится в Stories
      final ui.Image image = await boundary.toImage(pixelRatio: 3.0);
      final ByteData? byteData = await image.toByteData(format: ui.ImageByteFormat.png);
      if (byteData == null) throw Exception('toByteData failed');
      final Uint8List pngBytes = byteData.buffer.asUint8List();

      // Сохраняем во временную папку — Android share требует файл, не bytes напрямую
      final tempDir = await getTemporaryDirectory();
      final file = await File('${tempDir.path}/duet_pairing_${DateTime.now().millisecondsSinceEpoch}.png').create();
      await file.writeAsBytes(pngBytes);

      // Без параметра text — tagline уже зашит на самом изображении,
      // WhatsApp/Telegram не должны добавлять дублирующую подпись.
      await Share.shareXFiles(
        [XFile(file.path, mimeType: 'image/png')],
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: const Text('Не удалось поделиться. Попробуйте ещё раз.'),
            backgroundColor: Colors.red.shade800,
            behavior: SnackBarBehavior.floating,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            margin: const EdgeInsets.only(bottom: 80, left: 16, right: 16),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _isSharing = false);
    }
  }

  Widget _buildDishHeader() {
    final dish = _response?.dish ?? widget.dish ?? '';
    final mode = _response?.mode ?? widget.mode ?? 'food_to_alcohol';
    final budget = _response?.budget ?? widget.budget ?? 'medium';

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      child: Row(
        children: [
          Text(
            mode == 'food_to_alcohol' ? '🍽️' : '🥂',
            style: const TextStyle(fontSize: 28),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  mode == 'food_to_alcohol' ? 'Блюдо' : 'Напиток',
                  style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12),
                ),
                const SizedBox(height: 2),
                Text(
                  dish,
                  style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
                ),
                // Бейдж бюджета показываем только в режиме "блюдо → напиток".
                // В режиме "напиток → еда" бюджет не выбирается на главном экране,
                // поэтому и здесь его не показываем (был орфан "💰💰 Средний").
                if (mode == 'food_to_alcohol') ...[
                  const SizedBox(height: 4),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: _gold.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(
                      budget == 'budget' ? '💰 Бюджетно'
                          : budget == 'premium' ? '💰💰💰 Премиум'
                          : '💰💰 Средний',
                      style: const TextStyle(color: _gold, fontSize: 11, fontWeight: FontWeight.w600),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildResultsLabel() {
    final mode = _response?.mode ?? widget.mode ?? 'food_to_alcohol';
    return Text(
      mode == 'food_to_alcohol' ? 'Подходящие напитки' : 'Подходящие блюда',
      style: TextStyle(
        color: Colors.white.withOpacity(0.5),
        fontSize: 13,
        fontWeight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
    );
  }

  // ── Скелетон ────────────────────────────────────────────────────────────────

  Widget _buildSkeletonCard() {
    return AnimatedBuilder(
      animation: _pulseController,
      builder: (_, __) {
        final op = 0.05 + _pulseController.value * 0.07;
        return Container(
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: _card,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: Colors.white.withOpacity(0.06)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                _skel(32, 32, op, radius: 8),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _skel(10, 60, op),
                      const SizedBox(height: 6),
                      _skel(15, 150, op),
                      const SizedBox(height: 5),
                      _skel(12, 100, op),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                _skel(26, 56, op, radius: 8),
              ]),
              const SizedBox(height: 16),
              _skelLine(14, op),
              const SizedBox(height: 6),
              _skelLine(14, op),
              const SizedBox(height: 4),
              _skelLine(14, op, fraction: 0.65),
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(op * 0.6),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Row(children: [
                  _skel(14, 14, op, radius: 4),
                  const SizedBox(width: 8),
                  Expanded(child: _skel(12, 0, op)),
                ]),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _skel(double h, double w, double op, {double radius = 6}) {
    return Container(
      height: h,
      width: w == 0 ? null : w,
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(op),
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }

  Widget _skelLine(double h, double op, {double fraction = 1.0}) {
    return Row(children: [
      Expanded(
        flex: (fraction * 100).round(),
        child: _skel(h, 0, op),
      ),
      if (fraction < 1.0) Expanded(flex: ((1 - fraction) * 100).round(), child: const SizedBox()),
    ]);
  }

  // ── Ошибка ──────────────────────────────────────────────────────────────────

  Widget _buildError() {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 40),
      child: Column(
        children: [
          // Нейтральная info-иконка вместо wifi_off — ошибка может быть и
          // не сетевой (невалидный запрос, ошибка парсинга, лимит и т.д.).
          // Wi-Fi иконка вводила в заблуждение про "нет интернета".
          Icon(Icons.info_outline_rounded, color: Colors.white.withOpacity(0.3), size: 48),
          const SizedBox(height: 16),
          Text(
            _error ?? 'Что-то пошло не так',
            style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 14, height: 1.5),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 24),
          ElevatedButton(
            onPressed: () {
              setState(() { _isLoading = true; _error = null; });
              _startStream();
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: _gold,
              foregroundColor: _bg,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
              elevation: 0,
            ),
            child: const Text('Повторить', style: TextStyle(fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
  }

  // ── Карточка результата ──────────────────────────────────────────────────────

  Widget _buildResultCard(int index, PairingResult result) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: index == 1 ? _gold.withOpacity(0.4) : Colors.white.withOpacity(0.06),
          width: index == 1 ? 1.5 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildCardHeader(index, result),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  result.reason,
                  style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 14, height: 1.5),
                ),
                // Блок "Почему это работает" — только в Эксперт-режиме.
                // whyItWorks приходит с бэкенда только когда detail_level == expert.
                if (result.whyItWorks != null) ...[
                  const SizedBox(height: 12),
                  _buildWhyItWorksBlock(result.whyItWorks!),
                ],
                const SizedBox(height: 12),
                _buildBottomRow(result),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCardHeader(int index, PairingResult result) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          Container(
            width: 32,
            height: 32,
            decoration: BoxDecoration(
              color: index == 1 ? _gold : Colors.white.withOpacity(0.08),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Center(
              child: Text(
                '$index',
                style: TextStyle(
                  color: index == 1 ? _bg : Colors.white38,
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(result.resolvedEmoji, style: const TextStyle(fontSize: 14)),
                    const SizedBox(width: 4),
                    Flexible(
                      child: Text(
                        result.alcoholType,
                        style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  result.name,
                  style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w700),
                ),
                GestureDetector(
                  onTap: () => _openBuyLink(result.brand),
                  child: Row(
                    children: [
                      Flexible(
                        child: Text(
                          result.brand,
                          style: const TextStyle(color: _goldText, fontSize: 13),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: 4),
                      const Icon(Icons.open_in_new_rounded, size: 12, color: _goldText),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          // Правый верхний угол карточки: бейдж "Лучший выбор" (только для первой)
          // + priceRange под ним. Стандарт Booking/Airbnb — бейдж в углу карточки
          // часть самой карточки, не оторванный элемент сверху.
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (index == 1) ...[
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: _card,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: _gold.withOpacity(0.5), width: 1),
                  ),
                  child: const Text(
                    'Лучший выбор',
                    style: TextStyle(
                      color: _gold,
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 0.2,
                    ),
                  ),
                ),
                const SizedBox(height: 6),
              ],
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 110),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.05),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    result.priceRange,
                    style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 12),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildWhyItWorksBlock(String text) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: _gold.withOpacity(0.06),
        borderRadius: BorderRadius.circular(10),
        border: Border(
          left: BorderSide(color: _gold, width: 3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.science_rounded, color: _gold, size: 14),
              const SizedBox(width: 6),
              Text(
                'ПОЧЕМУ ЭТО РАБОТАЕТ',
                style: TextStyle(
                  color: _gold,
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.8,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            text,
            style: TextStyle(
              color: Colors.white.withOpacity(0.75),
              fontSize: 13,
              height: 1.5,
              fontStyle: FontStyle.italic,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBottomRow(PairingResult result) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.04),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          const Text('💡', style: TextStyle(fontSize: 14)),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              result.servingTip,
              style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 13, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _openBuyLink(String brand) async {
    final region = _response?.region ?? '';
    final encoded = Uri.encodeComponent(brand);
    Uri uri;

    switch (region) {
      case 'Казахстан':
        uri = Uri.parse('https://kaspi.kz/shop/search/?q=$encoded');
        break;
      case 'Россия':
        uri = Uri.parse('https://winestyle.ru/search/?search=$encoded');
        break;
      case 'Украина':
        uri = Uri.parse('https://www.google.com/search?q=$encoded+купить+Киев');
        break;
      case 'Беларусь':
        uri = Uri.parse('https://www.google.com/search?q=$encoded+купить+Минск');
        break;
      default:
        uri = Uri.parse('https://www.google.com/search?q=$encoded+купить');
    }

    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  Widget _buildSaveButton(BuildContext context) {
    // Sequential fade через явный state machine + AnimatedOpacity.
    // Почему не AnimatedSwitcher: даже с Interval(0.5,1.0) cross-fade держал
    // оба текста в дереве одновременно — буквы "ть"/"но" накладывались и было
    // видно перепечатку концовки. Здесь: opacity 1→0 за 160мс (старый исчез),
    // потом setState меняет текст в полной невидимости, потом opacity 0→1
    // за 160мс (новый появляется). Ни одного кадра с обоими текстами.
    return SizedBox(
      width: double.infinity,
      height: 54,
      child: ElevatedButton(
        onPressed: _isSaved ? () {} : () async {
          HapticFeedback.mediumImpact();
          if (_response == null) return;
          try {
            await ApiService.saveFavorite(_response!);
            if (!mounted) return;
            setState(() => _fadingOut = true);
            await Future.delayed(const Duration(milliseconds: 160));
            if (!mounted) return;
            setState(() {
              _isSaved = true;
              _fadingOut = false;
            });
          } catch (e) {
            if (mounted) {
              ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                content: Text(e.toString().replaceAll('Exception: ', '')),
                backgroundColor: Colors.red.shade800,
                behavior: SnackBarBehavior.floating,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                margin: const EdgeInsets.only(bottom: 80, left: 16, right: 16),
              ));
            }
          }
        },
        style: ElevatedButton.styleFrom(
          backgroundColor: _gold,
          foregroundColor: _bg,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          elevation: 0,
        ),
        child: AnimatedOpacity(
          opacity: _fadingOut ? 0.0 : 1.0,
          duration: const Duration(milliseconds: 160),
          curve: Curves.easeInOut,
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(_isSaved ? Icons.star_rounded : Icons.star_outline_rounded, size: 20),
              const SizedBox(width: 8),
              Text(
                _isSaved ? 'Сохранено в избранное' : 'Сохранить в избранное',
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
