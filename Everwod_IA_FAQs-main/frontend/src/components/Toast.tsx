import { useEffect } from 'react';
import { CheckCircle, XCircle, X } from 'lucide-react';
import { cn } from '../utils/format';

interface ToastProps {
  message: string;
  type: 'success' | 'error';
  onClose: () => void;
  isVisible: boolean;
}

export function Toast({ message, type, onClose, isVisible }: ToastProps) {
  useEffect(() => {
    if (isVisible) {
      const timer = setTimeout(() => {
        onClose();
      }, 4000);
      return () => clearTimeout(timer);
    }
  }, [isVisible, onClose]);

  if (!isVisible) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 animate-in slide-in-from-bottom-5 fade-in duration-300">
      <div className={cn(
        "flex items-center p-4 rounded-xl shadow-lg border",
        type === 'success' ? "bg-emerald-50 border-emerald-200 text-emerald-800" : "bg-rose-50 border-rose-200 text-rose-800"
      )}>
        {type === 'success' ? (
          <CheckCircle className="w-5 h-5 mr-3 shrink-0 text-emerald-600" />
        ) : (
          <XCircle className="w-5 h-5 mr-3 shrink-0 text-rose-600" />
        )}
        <p className="text-sm font-medium pr-6">{message}</p>
        <button 
          onClick={onClose}
          className="p-1 hover:bg-black/5 rounded-full transition-colors absolute right-3"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
