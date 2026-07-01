import requests
import re
import base64
import json
import time
import logging
import os
import csv
from typing import Optional, Dict, Any, List
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7327856614:AAG9fY6rjp_wPKTLNnQCgoZdzagla3h9-80"
AUTHORIZED_USER_ID = 6843321125

WAITING_FOR_URLS, WAITING_FOR_BULK_URLS, WAITING_FOR_FILE = range(3)

PREMIUM_STICKERS = {
    'welcome': 'CAACAgIAAxkBAA...',
    'working': 'CAACAgIAAxkBAA...',
    'error': 'CAACAgIAAxkBAA...',
    'dead': 'CAACAgIAAxkBAA...',
    'invalid': 'CAACAgIAAxkBAA...',
    'complete': 'CAACAgIAAxkBAA...',
    'processing': 'CAACAgIAAxkBAA...',
    'greeting': 'CAACAgIAAxkBAA...',
}

def smart_link_extractor(text: str) -> List[str]:
    trailing_chars_to_remove = r'[\]\[\)\}\{\.\,\;\:\!\?\|\/\\\'\"\s]+$'
    url_pattern = r'https?://[^\s\]\[\)\}\{\.\,\;\:\!\?\|\/\\\'\"<>]+(?:\.[^\s\]\[\)\}\{\.\,\;\:\!\?\|\/\\\'\"<>]+)*(?:\/[^\s\]\[\)\}\{\.\,\;\:\!\?\|\/\\\'\"<>]*)?'
    potential_urls = re.findall(url_pattern, text, re.IGNORECASE)
    cleaned_urls = []
    seen_urls = set()
    
    for url in potential_urls:
        url = re.sub(r'^[\[\(\{\s]+', '', url)
        url = re.sub(trailing_chars_to_remove, '', url)
        url = re.sub(r'\.{2,}$', '', url)
        
        if url.startswith(('http://', 'https://')) and len(url) > 15:
            if url not in seen_urls:
                seen_urls.add(url)
                cleaned_urls.append(url)
    
    return cleaned_urls


class ManualPayPalChecker:
    def __init__(self):
        self.ua = UserAgent()
        self.session = self._create_session()
        os.makedirs('responses', exist_ok=True)
        os.makedirs('results', exist_ok=True)

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=1,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=5, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def detect_payment_gateway(self, html: str, url: str) -> Dict[str, Any]:
        html_lower = html.lower()
        url_lower = url.lower()
        
        gateways = {
            'paypal': {'patterns': ['paypal.com', 'paypalobjects.com', 'paypal.me', 'paypal', 'braintree'], 'name': 'PayPal'},
            'stripe': {'patterns': ['stripe.com', 'js.stripe.com', 'stripe.network', 'stripe'], 'name': 'Stripe'},
            'authorize.net': {'patterns': ['authorize.net', 'accept.bluepay', 'authorize'], 'name': 'Authorize.Net'},
            'square': {'patterns': ['squareup.com', 'squarecdn.com', 'square.com'], 'name': 'Square'},
            'braintree': {'patterns': ['braintreegateway.com', 'braintreepayments.com'], 'name': 'Braintree'},
            '2checkout': {'patterns': ['2checkout.com', 'avangate.com'], 'name': '2Checkout'},
            'adyen': {'patterns': ['adyen.com', 'checkoutshopper'], 'name': 'Adyen'},
            'mollie': {'patterns': ['mollie.com'], 'name': 'Mollie'},
            'razorpay': {'patterns': ['razorpay.com'], 'name': 'Razorpay'},
            'klarna': {'patterns': ['klarna.com'], 'name': 'Klarna'},
            'shopify': {'patterns': ['shopify.com', 'myshopify.com'], 'name': 'Shopify'},
            'woocommerce': {'patterns': ['woocommerce.com', 'wc-ajax'], 'name': 'WooCommerce'},
            'coinbase': {'patterns': ['coinbase.com', 'commerce.coinbase'], 'name': 'Coinbase'},
            'bitpay': {'patterns': ['bitpay.com'], 'name': 'BitPay'}
        }
        
        detected_gateways = []
        gateway_details = {}
        
        for gateway_id, gateway_info in gateways.items():
            for pattern in gateway_info['patterns']:
                if pattern in html_lower or pattern in url_lower:
                    detected_gateways.append({'id': gateway_id, 'name': gateway_info['name']})
                    break
        
        if detected_gateways:
            primary = detected_gateways[0]
            gateway_details = {
                'primary_gateway': primary['name'],
                'all_detected': [g['name'] for g in detected_gateways],
                'gateway_count': len(detected_gateways)
            }
            if primary['id'] == 'paypal':
                gateway_details.update(self._extract_paypal_specific_details(html))
            elif primary['id'] == 'stripe':
                gateway_details.update(self._extract_stripe_specific_details(html))
        else:
            gateway_details = {'primary_gateway': 'Unknown', 'all_detected': [], 'gateway_count': 0}
        
        return gateway_details

    def _extract_paypal_specific_details(self, html: str) -> Dict[str, Any]:
        details = {}
        merchant_match = re.search(r'"merchantId":"([^"]+)"', html)
        if merchant_match: details['merchant_id'] = merchant_match.group(1)
        merchant_name = re.search(r'"businessName":"([^"]+)"', html) or re.search(r'"merchantName":"([^"]+)"', html)
        if merchant_name: details['merchant_name'] = merchant_name.group(1)
        amount_match = re.search(r'"amount":"([^"]+)"', html) or re.search(r'"value":"([\d.]+)"', html)
        if amount_match: details['amount'] = amount_match.group(1)
        currency_match = re.search(r'"currencyCode":"([^"]+)"', html) or re.search(r'"currency":"([^"]+)"', html)
        if currency_match: details['currency'] = currency_match.group(1)
        intent_match = re.search(r'"intent":"([^"]+)"', html)
        if intent_match: details['intent'] = intent_match.group(1)
        return details

    def _extract_stripe_specific_details(self, html: str) -> Dict[str, Any]:
        details = {}
        pk_match = re.search(r'pk_live_[a-zA-Z0-9]+', html)
        if pk_match: details['stripe_key'] = pk_match.group(0)[:20] + '...'
        amount_match = re.search(r'"amount":(\d+)', html)
        if amount_match:
            details['amount'] = str(int(amount_match.group(1)) / 100)
            details['currency'] = 'USD'
        return details

    def check_single_url(self, url: str) -> Dict[str, Any]:
        if not url.startswith(('http://', 'https://')):
            return {'url': url, 'status': 'invalid', 'reason': 'Invalid URL'}

        headers = {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        try:
            response = self.session.get(url, headers=headers, timeout=15)
            gateway_details = self.detect_payment_gateway(response.text, url)
            error_msg = self._extract_error_message(response.text)
            
            result = {
                'url': url,
                'status_code': response.status_code,
                'error_message': error_msg,
                'gateway_details': gateway_details
            }

            enc_match = re.search(r'"data-client-token":"(.*?)"', response.text)
            if enc_match:
                try:
                    decoded = base64.b64decode(enc_match.group(1)).decode('utf-8')
                    token_match = re.search(r'"accessToken":"(.*?)"', decoded)
                    if token_match:
                        result['status'] = 'working'
                        result['access_token'] = token_match.group(1)[:50] + '...'
                        result['full_token'] = token_match.group(1)
                        result['reason'] = 'Access token found - valid payment link'
                    else:
                        result['status'] = 'invalid'
                        result['reason'] = 'Client token exists but no access token'
                except Exception:
                    result['status'] = 'invalid'
                    result['reason'] = 'Failed to decode client token'
            else:
                if error_msg:
                    result['status'] = 'error'
                    result['reason'] = f'Error/Decline message: {error_msg}'
                else:
                    result['status'] = 'invalid'
                    result['reason'] = 'No client token or clear error message'

            if response.status_code >= 400:
                result['status'] = 'dead'
                result['reason'] = f'HTTP {response.status_code}'

            return result
        except requests.exceptions.ConnectionError:
            return {'url': url, 'status': 'dead', 'reason': 'DNS lookup failed'}
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {'url': url, 'status': 'error', 'reason': str(e)[:100]}

    def _extract_error_message(self, html: str) -> Optional[str]:
        patterns = [
            r'\[Authorize\.Net\]\s*(.*?)(?:<|$)',
            r'(?:declined|error|invalid|failed|refused).*?(?:transaction|payment).*?[^.]*\.',
            r'<div[^>]*class="[^"]*error[^"]*"[^>]*>(.*?)</div>',
            r'"errorMessage":"(.*?)"',
            r'<title>(.*?(?:error|declined|failed).*?)</title>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                msg = match.group(1) if match.lastindex else match.group(0)
                clean_msg = re.sub(r'<[^>]+>', '', msg).strip()
                if clean_msg and len(clean_msg) > 5:
                    return clean_msg[:300]
        return None

    def save_result_to_csv(self, result: Dict[str, Any], filename: str = 'results/all_results.csv'):
        file_exists = os.path.isfile(filename)
        
        with open(filename, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['timestamp', 'url', 'status', 'status_code', 'gateway', 'merchant', 'amount', 'currency', 'reason', 'access_token']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
            
            gw = result.get('gateway_details', {})
            writer.writerow({
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'url': result.get('url', ''),
                'status': result.get('status', ''),
                'status_code': result.get('status_code', ''),
                'gateway': gw.get('primary_gateway', ''),
                'merchant': gw.get('merchant_name', ''),
                'amount': gw.get('amount', ''),
                'currency': gw.get('currency', ''),
                'reason': (result.get('reason', '') or '')[:200],
                'access_token': result.get('access_token', '')
            })

    def save_working_link(self, result: Dict[str, Any]):
        try:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            gateway = result.get('gateway_details', {}).get('primary_gateway', 'Unknown')
            with open('results/working_links.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {result['url']}\n")
                f.write(f"    Gateway: {gateway}\n")
                if result.get('access_token'): f.write(f"    Token: {result['access_token']}\n")
                if result.get('full_token'): f.write(f"    Full Token: {result['full_token']}\n")
                f.write("-" * 50 + "\n")
        except Exception as e:
            logger.error(f"Error saving working link: {e}")


checker = ManualPayPalChecker()

async def send_premium_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE, sticker_type: str):
    sticker_id = PREMIUM_STICKERS.get(sticker_type)
    if sticker_id and sticker_id != 'CAACAgIAAxkBAA...':
        try:
            await update.message.reply_sticker(sticker=sticker_id)
        except Exception as e:
            logger.error(f"Failed to send premium sticker: {e}")

def format_telegram_message(result: Dict[str, Any], link_number: int = 0, total_links: int = 0) -> str:
    gw = result.get('gateway_details', {})
    status_text = result['status'].upper()
    
    counter = f"Link {link_number}/{total_links}\n\n" if link_number > 0 and total_links > 0 else ""
    
    msg = f"{counter}"
    msg += f"Gateway: {gw.get('primary_gateway', 'Unknown')}\n\n"
    msg += f"Status: {status_text}\n"
    msg += f"URL: {result['url'][:80]}\n"
    
    if result.get('status_code'):
        msg += f"HTTP: {result['status_code']}\n"
    if result.get('reason'):
        msg += f"Reason: {result['reason'][:200]}\n"
    if gw.get('merchant_name'):
        msg += f"Merchant: {gw['merchant_name']}\n"
    if gw.get('amount'):
        msg += f"Amount: {gw['amount']} {gw.get('currency', 'USD')}\n"
    if result.get('access_token'):
        msg += f"Token: {result['access_token']}\n"
    
    return msg

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return

    await send_premium_sticker(update, context, 'welcome')

    keyboard = [
        [InlineKeyboardButton("Check Single URL", callback_data='check_single')],
        [InlineKeyboardButton("Check Multiple URLs (Paste)", callback_data='check_bulk')],
        [InlineKeyboardButton("Check URLs from File", callback_data='check_file')],
        [InlineKeyboardButton("Show Working Links", callback_data='show_working')],
        [InlineKeyboardButton("Clear Results", callback_data='clear_results')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Payment Gateway Checker Bot\n\n"
        "Each link is checked and results appear instantly\n"
        "Results are automatically saved to CSV\n\n"
        "Select operation:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != AUTHORIZED_USER_ID:
        await query.edit_message_text("Unauthorized.")
        return ConversationHandler.END

    if query.data == 'check_single':
        await send_premium_sticker(update, context, 'greeting')
        await query.edit_message_text("Send the URL you want to check:")
        return WAITING_FOR_URLS

    elif query.data == 'check_bulk':
        await query.edit_message_text(
            "Paste the URLs you want to check\n"
            "One URL per line (or separated by spaces)\n"
            "Bot will extract URLs automatically\n\n"
            "Send the URLs now:"
        )
        return WAITING_FOR_BULK_URLS

    elif query.data == 'check_file':
        await query.edit_message_text(
            "Send a TXT or CSV file containing URLs\n\n"
            "Automatic URL extraction\n"
            "Extra characters and symbols are ignored\n"
            "Each link result appears instantly"
        )
        return WAITING_FOR_FILE

    elif query.data == 'show_working':
        try:
            with open('results/working_links.txt', 'r', encoding='utf-8') as f:
                content = f.read()
            if not content:
                await query.edit_message_text("No working links saved yet.")
            else:
                await query.message.delete()
                for chunk in [content[i:i+4000] for i in range(0, len(content), 4000)]:
                    await context.bot.send_message(chat_id=user_id, text=f"```\n{chunk}\n```", parse_mode='Markdown')
        except FileNotFoundError:
            await query.edit_message_text("Working links file does not exist yet.")
        return ConversationHandler.END

    elif query.data == 'clear_results':
        keyboard = [
            [InlineKeyboardButton("Yes, delete files", callback_data='confirm_clear')],
            [InlineKeyboardButton("Cancel", callback_data='cancel_clear')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Are you sure you want to delete all saved results?\n\n"
            "working_links.txt\n"
            "all_results.csv",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

async def confirm_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    files_to_delete = ['results/working_links.txt', 'results/all_results.csv']
    deleted = []
    not_found = []
    
    for file in files_to_delete:
        if os.path.exists(file):
            os.remove(file)
            deleted.append(file)
        else:
            not_found.append(file)
    
    msg = "Deleted:\n" + "\n".join([f"{f}" for f in deleted]) if deleted else ""
    msg += "\n\nNot found:\n" + "\n".join([f"{f}" for f in not_found]) if not_found else ""
    
    await query.edit_message_text(msg)

async def cancel_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Operation cancelled.")

async def handle_single_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    msg = await update.message.reply_text("Checking URL...")
    await send_premium_sticker(update, context, 'processing')
    
    result = checker.check_single_url(url)
    
    checker.save_result_to_csv(result)
    if result['status'] == 'working':
        checker.save_working_link(result)
        await send_premium_sticker(update, context, 'working')
    elif result['status'] in ['error', 'dead']:
        await send_premium_sticker(update, context, result['status'])
    
    if result['status'] == 'working':
        response_text = format_telegram_message(result)
        await msg.edit_text(response_text, disable_web_page_preview=True)
    else:
        await msg.edit_text(f"Check Complete. Status: {result['status']}")
    
    if result.get('status') == 'working' and result.get('full_token'):
        await update.message.reply_text(f"Full Access Token:\n```\n{result['full_token']}\n```", parse_mode='Markdown')
    
    return ConversationHandler.END

async def handle_bulk_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    
    extracted_urls = smart_link_extractor(text)
    
    if not extracted_urls:
        await update.message.reply_text("No valid URLs found in the text.")
        return WAITING_FOR_BULK_URLS
    
    total = len(extracted_urls)
    await send_premium_sticker(update, context, 'processing')
    status_msg = await update.message.reply_text(f"Found {total} URLs\nChecking...")
    
    working_count = 0
    invalid_count = 0
    error_count = 0
    dead_count = 0
    
    for i, url in enumerate(extracted_urls, 1):
        try:
            result = checker.check_single_url(url)
            
            checker.save_result_to_csv(result)
            if result['status'] == 'working':
                checker.save_working_link(result)
            
            if result['status'] == 'working':
                working_count += 1
            elif result['status'] == 'invalid':
                invalid_count += 1
            elif result['status'] == 'error':
                error_count += 1
            elif result['status'] == 'dead':
                dead_count += 1
            
            if result['status'] == 'working':
                result_text = format_telegram_message(result, i, total)
                await update.message.reply_text(result_text, disable_web_page_preview=True)
            
            if result.get('status') == 'working' and result.get('full_token'):
                await update.message.reply_text(
                    f"Full Token for link {i}:\n```\n{result['full_token']}\n```",
                    parse_mode='Markdown'
                )
            
            if i % 5 == 0 or i == total:
                await status_msg.edit_text(
                    f"Progress: {i}/{total}\n"
                    f"Working: {working_count} | Invalid: {invalid_count}\n"
                    f"Error: {error_count} | Dead: {dead_count}"
                )
            
            if i < total:
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Error checking URL {url}: {e}")
            await update.message.reply_text(f"Error checking: {url[:50]}")
    
    await send_premium_sticker(update, context, 'complete')
    
    summary = (
        f"Check Complete!\n\n"
        f"Total: {total}\n"
        f"Working: {working_count}\n"
        f"Invalid: {invalid_count}\n"
        f"Errors: {error_count}\n"
        f"Dead: {dead_count}\n\n"
        f"Results saved in:\n"
        f"results/working_links.txt\n"
        f"results/all_results.csv"
    )
    await status_msg.edit_text(summary)
    
    if os.path.exists('results/all_results.csv'):
        with open('results/all_results.csv', 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"scan_results_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                caption="Complete results file"
            )
    
    return ConversationHandler.END

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = await update.message.reply_text("Reading file...")
    
    try:
        file = await update.message.document.get_file()
        temp_file_path = f"/tmp/{file.file_id}_{update.message.document.file_name}"
        await file.download_to_drive(temp_file_path)

        extracted_urls = []
        seen_urls = set()
        
        # Read file line by line to avoid memory issues with large files
        with open(temp_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                urls_in_line = smart_link_extractor(line)
                for url in urls_in_line:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        extracted_urls.append(url)
        
        os.remove(temp_file_path) # Clean up the temporary file
        
        if not extracted_urls:
            await msg.edit_text("No valid URLs found in the file.")
            return ConversationHandler.END
        
        total = len(extracted_urls)
        await send_premium_sticker(update, context, 'processing')
        await msg.edit_text(f"Extracted {total} URLs\nStarting check...")
        
        working_count = 0
        invalid_count = 0
        error_count = 0
        dead_count = 0
        
        status_msg = await update.message.reply_text(f"0/{total}")
        
        for i, url in enumerate(extracted_urls, 1):
            try:
                result = checker.check_single_url(url)
                
                checker.save_result_to_csv(result)
                if result['status'] == 'working':
                    checker.save_working_link(result)
                
                if result['status'] == 'working':
                    working_count += 1
                elif result['status'] == 'invalid':
                    invalid_count += 1
                elif result['status'] == 'error':
                    error_count += 1
                elif result['status'] == 'dead':
                    dead_count += 1
                
                if result['status'] == 'working':
                    result_text = format_telegram_message(result, i, total)
                    await update.message.reply_text(result_text, disable_web_page_preview=True)
                
                if result.get('status') == 'working' and result.get('full_token'):
                    await update.message.reply_text(
                        f"Full Token for link {i}:\n```\n{result['full_token']}\n```",
                        parse_mode='Markdown'
                    )
                
                if i % 10 == 0 or i == total:
                    await status_msg.edit_text(
                        f"Progress: {i}/{total}\n"
                        f"Working: {working_count} | Invalid: {invalid_count}\n"
                        f"Error: {error_count} | Dead: {dead_count}"
                    )
                

                    
            except Exception as e:
                logger.error(f"Error checking URL {url}: {e}")
        
        await send_premium_sticker(update, context, 'complete')
        
        summary = (
            f"File Check Complete!\n\n"
            f"Filename: {update.message.document.file_name}\n"
            f"Total URLs: {total}\n\n"
            f"Working: {working_count}\n"
            f"Invalid: {invalid_count}\n"
            f"Errors: {error_count}\n"
            f"Dead: {dead_count}\n\n"
            f"Results saved in:\n"
            f"results/working_links.txt\n"
            f"results/all_results.csv"
        )
        await status_msg.edit_text(summary)
        
        if os.path.exists('results/all_results.csv'):
            with open('results/all_results.csv', 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"scan_results_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                    caption="Complete results file"
                )
        
        await msg.delete()
        
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        await msg.edit_text(f"Error occurred: {str(e)[:100]}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    
    application.add_handler(CallbackQueryHandler(confirm_clear, pattern='^confirm_clear$'))
    application.add_handler(CallbackQueryHandler(cancel_clear, pattern='^cancel_clear$'))

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^(check_single|check_bulk|check_file|show_working|clear_results)$')],
        states={
            WAITING_FOR_URLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_single_url)],
            WAITING_FOR_BULK_URLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bulk_urls)],
            WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, handle_file_upload)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
