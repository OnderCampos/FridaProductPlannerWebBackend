from typing import TypedDict, List, Dict, Any, Optional

from pydantic import BaseModel
from typing import Optional

class UIStoryState(TypedDict):
    # Inputs iniciales
    project_description: str # Descripción del proyecto proporcionada por el usuario
    image_base64: List[str] # Imagen de la interfaz de usuario en formato base64

    epic_name: str 
    template_field_keys: List[str]
    template_fields_json: str
    fields_description: str
    
    # Se llenan durante el flujo
    ui_analysis: Optional[str] # Análisis de la imagen de la interfaz de usuario generado por el modelo
    user_stories: Optional[List[Dict[str, Any]]] # Lista de historias de usuario generadas por el modelo, cada historia es un diccionario con detalles como título, descripción, criterios de aceptación, etc.
    error: Optional[str] # Mensaje de error en caso de que ocurra algún problema durante el procesamiento
    retry_count: int