import fitz
from airbyte_cdk.logger import init_logger

logger = init_logger("airbyte")

class PdfDownloadMiddleware:

    def process_response(self, response):
        try:
            pdf_document = fitz.open(stream=response.body, filetype="pdf")
            text = ""
            for page in pdf_document:
                text += page.get_text()
            return text
        except Exception as e:
            # Log the exception if needed
            logger.error(f"Error while processing pdf: {e}")
            return ""
