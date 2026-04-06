import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/pairing_result.dart';
import 'auth_service.dart';

class ApiService {
  static const String _baseUrl = 'https://duet-app-production.up.railway.app';

  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getIdToken();
    return {
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  // ── Подборка (стриминг) ───────────────────────────────────────────────────

  static Stream<String> pairStream({
    required String dish,
    required String mode,
    required String budget,
  }) async* {
    final prefs = await SharedPreferences.getInstance();
    final region = prefs.getString('region') ?? 'СНГ';
    final detailLevel = prefs.getString('detail_level') ?? 'standard';
    final headers = await _headers();

    final client = http.Client();
    try {
      final request = http.Request('POST', Uri.parse('$_baseUrl/pair/stream'));
      request.headers.addAll(headers);
      request.body = jsonEncode({
        'dish': dish,
        'mode': mode,
        'budget': budget,
        'region': region,
        'detail_level': detailLevel,
      });

      final streamed = await client.send(request).timeout(const Duration(seconds: 30));

      if (streamed.statusCode == 429) {
        throw Exception('Достигнут лимит подборок. Перейдите на Premium для безлимитного доступа.');
      }
      if (streamed.statusCode == 401) {
        throw Exception('Ошибка авторизации. Попробуйте выйти и войти снова.');
      }
      if (streamed.statusCode != 200) {
        throw Exception('Сервис временно недоступен. Попробуйте через минуту.');
      }

      await for (final chunk in streamed.stream.transform(utf8.decoder)) {
        yield chunk;
      }
    } finally {
      client.close();
    }
  }

  // ── История ───────────────────────────────────────────────────────────────

  static Future<List<PairingResponse>> getHistory() async {
    final headers = await _headers();
    final response = await http
        .get(Uri.parse('$_baseUrl/history'), headers: headers)
        .timeout(const Duration(seconds: 15));

    if (response.statusCode != 200) return [];
    final List<dynamic> data = jsonDecode(utf8.decode(response.bodyBytes));
    return data.map((r) => PairingResponse.fromJson(r)).toList();
  }

  // ── Избранное ─────────────────────────────────────────────────────────────

  static Future<List<PairingResponse>> getFavorites() async {
    final headers = await _headers();
    final response = await http
        .get(Uri.parse('$_baseUrl/favorites'), headers: headers)
        .timeout(const Duration(seconds: 15));

    if (response.statusCode != 200) return [];
    final List<dynamic> data = jsonDecode(utf8.decode(response.bodyBytes));
    return data.map((r) => PairingResponse.fromJson(r)).toList();
  }

  static Future<bool> saveFavorite(PairingResponse response) async {
    final headers = await _headers();
    final res = await http
        .post(
          Uri.parse('$_baseUrl/favorites'),
          headers: headers,
          body: jsonEncode({
            'dish': response.dish,
            'mode': response.mode,
            'budget': response.budget,
            'region': response.region,
            'results': response.results.map((r) => r.toJson()).toList(),
          }),
        )
        .timeout(const Duration(seconds: 15));

    if (res.statusCode == 429) {
      throw Exception('Лимит 10 избранных для Free. Перейдите на Premium.');
    }
    if (res.statusCode != 200) return false;
    final data = jsonDecode(res.body);
    return data['saved'] == true;
  }

  static Future<void> removeFavorite(int id) async {
    final headers = await _headers();
    await http
        .delete(Uri.parse('$_baseUrl/favorites/$id'), headers: headers)
        .timeout(const Duration(seconds: 15));
  }

  // ── Профиль / использование ───────────────────────────────────────────────

  static Future<Map<String, dynamic>?> getMe() async {
    final headers = await _headers();
    try {
      final response = await http
          .get(Uri.parse('$_baseUrl/me'), headers: headers)
          .timeout(const Duration(seconds: 10));
      if (response.statusCode != 200) return null;
      return jsonDecode(utf8.decode(response.bodyBytes));
    } catch (_) {
      return null;
    }
  }
}
